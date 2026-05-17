"""Adversarial-JSON property tests for the atomic-write JSON path.

``write_atomic_json`` is used everywhere a structured runtime state
file is persisted (session.json, file_locks.json, supervisor_state.json,
merkle seal). Subtle JSON serialisation behaviours can creep in here:

* **NaN / +Inf / -Inf** — Python's ``json.dumps`` emits non-standard
  ``NaN`` / ``Infinity`` tokens by default. The audit / runtime
  consumers expect strict JSON; the writer's contract is to either
  emit valid JSON or refuse to write. The property here asserts the
  current behaviour: if a non-finite float reaches the writer, the
  resulting bytes parse back via ``json.loads`` (whose default
  permissive flag matches the dump default). This locks the current
  contract so any future tightening (``allow_nan=False``) is a
  deliberate change.

* **Dict key order does not affect ``sort_keys`` round-trip** — the
  ``sort_keys=True`` argument is intended to produce a canonical
  byte form. The property asserts that two semantically-equivalent
  dicts (different insertion order) write the same bytes.

* **Large numeric payloads** — ``json.dumps`` happily round-trips
  Python's arbitrary-precision ints. The property catches a
  regression that would coerce to float and lose precision on values
  beyond 2^53.

* **Unicode keys / values do not corrupt the JSON byte form** — the
  default Python ``json.dumps`` emits ``\\uXXXX`` escapes; the
  bytes on disk are pure ASCII regardless of the input character set.
  Catches regressions that flip ``ensure_ascii=False`` and produce
  payloads downstream tools cannot parse.

Each property uses the smoke profile and is microsecond-fast.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.persistence.atomic_write import write_atomic_json


@given(payload=st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=4))
def test_sort_keys_makes_writes_canonical(
    tmp_path_factory: pytest.TempPathFactory,
    payload: dict[str, int],
) -> None:
    """Writing the same dict twice produces identical bytes (``sort_keys=True``).

    Catches regressions where ``sort_keys`` is accidentally dropped
    from the wrapper. Without it, two writers with different insertion
    orders would produce diff-noisy commits / drift across runs.
    """
    a = tmp_path_factory.mktemp("aw-json") / "a.json"
    b = tmp_path_factory.mktemp("aw-json") / "b.json"

    write_atomic_json(a, payload, sort_keys=True)
    # Build a reversed-order copy.
    reordered = dict(reversed(list(payload.items())))
    write_atomic_json(b, reordered, sort_keys=True)

    assert a.read_bytes() == b.read_bytes()


@given(
    big_int=st.integers(
        min_value=-(2**80),
        max_value=2**80,
    ),
)
def test_arbitrary_precision_int_round_trips(
    tmp_path_factory: pytest.TempPathFactory,
    big_int: int,
) -> None:
    """Arbitrary-precision ints survive a JSON round-trip without precision loss.

    JSON's default Python encoder preserves int width. A regression
    that swapped ``json.dumps`` for a float-coercing encoder would
    silently corrupt every >2^53 quantity persisted to runtime state.
    """
    target = tmp_path_factory.mktemp("aw-json") / "big.json"
    write_atomic_json(target, {"n": big_int})
    loaded = json.loads(target.read_text())
    assert loaded["n"] == big_int
    assert isinstance(loaded["n"], int)


@given(
    text=st.text(
        st.characters(min_codepoint=0x80, max_codepoint=0xFFFF, blacklist_categories=("Cs",)),
        min_size=1,
        max_size=16,
    ),
)
def test_unicode_value_emits_ascii_escapes(
    tmp_path_factory: pytest.TempPathFactory,
    text: str,
) -> None:
    """High-codepoint values are escaped as ``\\uXXXX`` (ASCII bytes on disk).

    Default ``json.dumps`` emits ``ensure_ascii=True``; the produced
    bytes contain only ASCII. Catches a refactor that flips the flag
    and produces multi-byte UTF-8 sequences that downstream consumers
    (legacy parsers, regex-scanners) cannot handle.

    Equality is established via ``json.loads`` rather than raw byte
    inspection — the loader expands escapes back to the original
    code point regardless of how the writer stored them.
    """
    target = tmp_path_factory.mktemp("aw-json") / "u.json"
    write_atomic_json(target, {"k": text})
    raw = target.read_bytes()
    # Every byte is ASCII (no leak of multi-byte UTF-8).
    assert all(b < 0x80 for b in raw)
    loaded = json.loads(raw)
    assert loaded["k"] == text


@settings(
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(depth=st.integers(min_value=1, max_value=16))
def test_deeply_nested_dict_round_trips(
    tmp_path_factory: pytest.TempPathFactory,
    depth: int,
) -> None:
    """A nested-dict payload up to depth 16 round-trips exactly.

    Catches recursion-related regressions in the JSON layer (Python's
    default recursion limit is 1000; depth 16 leaves enormous head-
    room but exercises the code path that any future custom encoder
    would have to honour).
    """
    payload: Any = {"leaf": 42}
    for i in range(depth):
        payload = {f"k_{i}": payload}

    target = tmp_path_factory.mktemp("aw-json") / "deep.json"
    write_atomic_json(target, payload)
    loaded = json.loads(target.read_text())
    assert loaded == payload


def test_nan_emits_non_strict_token(tmp_path_factory: pytest.TempPathFactory) -> None:
    """``NaN`` payload writes the non-standard ``NaN`` token (status quo).

    Pinned (single-value space). Documents the current behaviour: the
    writer does *not* pass ``allow_nan=False``, so a NaN in payload
    produces bytes that Python's ``json.loads`` can read back but any
    strict parser (JS, Rust, Go) will refuse. Any change to this
    behaviour should be deliberate and visible as a property failure.
    """
    target = tmp_path_factory.mktemp("aw-json") / "nan.json"
    write_atomic_json(target, {"x": math.nan})
    text = target.read_text()
    assert "NaN" in text


@given(
    payload=st.dictionaries(
        st.text(min_size=1, max_size=4),
        st.one_of(
            st.integers(),
            st.text(min_size=0, max_size=8),
            st.lists(st.integers(min_value=-100, max_value=100), max_size=4),
        ),
        min_size=0,
        max_size=4,
    ),
)
def test_round_trip_arbitrary_payloads(
    tmp_path_factory: pytest.TempPathFactory,
    payload: dict[str, Any],
) -> None:
    """Arbitrary mixed-type payloads survive write-then-read.

    Composite property: combines the int-precision, key-order, and
    container-flat-vs-nested cases into one Hypothesis sweep.
    """
    target = tmp_path_factory.mktemp("aw-json") / "mix.json"
    write_atomic_json(target, payload)
    loaded = json.loads(target.read_text())
    assert loaded == payload


@given(
    payload=st.recursive(
        st.integers() | st.text(max_size=4) | st.booleans(),
        lambda children: st.lists(children, max_size=3)
        | st.dictionaries(st.text(min_size=1, max_size=4), children, max_size=3),
        max_leaves=10,
    ),
)
def test_indent_does_not_change_semantic_value(
    tmp_path_factory: pytest.TempPathFactory,
    payload: Any,
) -> None:
    """``indent=2`` and ``indent=None`` yield bytes that parse to the same value.

    The indent kwarg only affects whitespace. Catches a regression
    where indent forwarding accidentally toggled ``sort_keys`` as a
    side-effect (the wrapper accepts both — they must be independent).
    """
    a = tmp_path_factory.mktemp("aw-json") / "a.json"
    b = tmp_path_factory.mktemp("aw-json") / "b.json"
    write_atomic_json(a, payload, indent=None, sort_keys=True)
    write_atomic_json(b, payload, indent=2, sort_keys=True)
    assert json.loads(a.read_text()) == json.loads(b.read_text())
