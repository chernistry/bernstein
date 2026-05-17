"""Adversarial-unicode property tests for the HMAC audit log.

The audit log canonicalises every entry via
``json.dumps(..., sort_keys=True)`` before computing its HMAC. That
canonicalisation must be byte-stable under:

* RTL / bidi / combining characters in details payloads
* NUL bytes embedded in strings (JSON allows ``\\u0000`` in strings)
* Mixed-case unicode normal forms (NFC vs NFD)
* Deeply nested dict / list structures
* Numeric edge cases — very large / negative ints

Existing coverage in ``test_audit_chain_bughunt.py`` exercises some
of these as single fixtures; the properties below are *parametric*
over Hypothesis-generated payloads so any future regression surfaces
on the first PR rather than once it lands in production.

The contract under test is: ``log()`` accepts arbitrary unicode in
``details`` values and ``query()`` returns the deserialised dict
equal to what was written. Equality is established through a
``json.dumps`` round-trip on both sides so JSON's int-vs-float
equivalence does not generate false failures.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.security.audit import AuditLog

# Adversarial unicode pool — RTL marks, combining marks, control codes,
# unusual scripts. We intentionally exclude surrogate pairs (``Cs``)
# because Python strings cannot contain lone surrogates and ``st.text``
# would invent them only via the ``surrogate_pairs_allowed`` flag.
_ADVERSARIAL = st.one_of(
    st.text(
        st.characters(blacklist_categories=("Cs",), min_codepoint=0x20, max_codepoint=0x10FFFF),
        min_size=0,
        max_size=64,
    ),
    st.sampled_from(
        [
            "",  # Empty
            "‎",  # LRM
            "‏",  # RLM
            "‮",  # RLO
            "\x00",  # NUL inside string
            "́",  # COMBINING ACUTE ACCENT
            "﻿",  # ZERO WIDTH NO-BREAK SPACE (BOM)
            "á",  # decomposed á (NFD)
            "á",  # composed á (NFC)
            "\U0001f600",  # emoji
            "🇺🇸",  # flag (regional indicator pair)
            "ё" * 32,  # mid-BMP repeat
            "𒀀" * 8,  # cuneiform — outside BMP
        ]
    ),
)


_KEY = st.text(
    st.characters(blacklist_categories=("Cs",), min_codepoint=0x20, max_codepoint=0x7E),
    min_size=1,
    max_size=8,
)


def _new_log() -> AuditLog:
    tmp = Path(tempfile.mkdtemp(prefix="bernstein-audit-unicode-"))
    audit_dir = tmp / "audit"
    audit_dir.mkdir()
    key_path = tmp / "audit.key"
    key_path.write_bytes(b"hypothesis-fuzz-key-32-bytes-padding-pad")
    key_path.chmod(0o600)
    return AuditLog(audit_dir=audit_dir, key_path=key_path)


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    payloads=st.lists(
        st.dictionaries(_KEY, _ADVERSARIAL, max_size=4),
        min_size=1,
        max_size=4,
    ),
)
def test_adversarial_unicode_chain_verifies(payloads: list[dict[str, Any]]) -> None:
    """Arbitrary-unicode details still produce a verifiable chain.

    Catches regressions where ``json.dumps`` is called without
    ``ensure_ascii=True`` on one side and with it on the other — the
    HMAC would then differ between writer and verifier on any non-ASCII
    string, breaking every chain that touches an emoji or RTL mark.
    """
    log = _new_log()
    for d in payloads:
        log.log("evt", "actor", "task", "rid", d)
    valid, errors = log.verify()
    assert valid, f"unicode chain rejected itself: {errors}"


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    payloads=st.lists(
        st.dictionaries(_KEY, _ADVERSARIAL, max_size=4),
        min_size=1,
        max_size=4,
    ),
)
def test_adversarial_unicode_query_round_trips(payloads: list[dict[str, Any]]) -> None:
    """``query()`` returns details equal to what was written.

    Compares both via direct equality and via a JSON round-trip on each
    side; either would fail if the writer canonicalises differently
    than the reader expects.
    """
    log = _new_log()
    for d in payloads:
        log.log("evt", "actor", "task", "rid", d)

    rows = log.query()
    assert len(rows) == len(payloads)
    for source, persisted in zip(payloads, rows, strict=False):
        assert source == persisted.details, (
            f"direct equality failed: source={source!r} persisted={persisted.details!r}"
        )
        assert json.loads(json.dumps(source, sort_keys=True)) == json.loads(
            json.dumps(persisted.details, sort_keys=True),
        )


@settings(
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    depth=st.integers(min_value=1, max_value=8),
)
def test_deeply_nested_details_chain(depth: int) -> None:
    """Nested dict payloads up to depth 8 still produce a verifiable chain.

    Catches recursion-bomb regressions in the canonicalisation path
    (e.g. an accidental ``json.dumps(..., indent=2)`` that explodes the
    payload size). 8 is well within Python's default recursion limit;
    the property is about *correctness* not *DoS resistance*.
    """
    payload: dict[str, Any] = {"leaf": True}
    for i in range(depth):
        payload = {f"level_{i}": payload}

    log = _new_log()
    log.log("evt", "actor", "task", "rid", payload)
    valid, errors = log.verify()
    assert valid, f"nested-depth chain rejected itself: {errors}"

    rows = log.query()
    assert rows[0].details == payload


@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    big_int=st.integers(min_value=-(2**64), max_value=2**64),
    long_str=st.text(min_size=0, max_size=256),
)
def test_extreme_numeric_and_string_values(big_int: int, long_str: str) -> None:
    """Large-magnitude integers and long strings round-trip cleanly.

    JSON encodes Python ints with arbitrary precision; this property
    guards against an accidental ``float`` coercion in a canonicaliser
    refactor that would corrupt every entry with a 17+ digit integer.
    """
    log = _new_log()
    log.log("evt", "a", "task", "rid", {"big": big_int, "text": long_str})

    valid, errors = log.verify()
    assert valid, errors

    rows = log.query()
    assert rows[0].details["big"] == big_int
    assert rows[0].details["text"] == long_str
