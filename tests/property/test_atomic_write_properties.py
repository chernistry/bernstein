"""Property tests for the atomic-write helpers.

``write_atomic_bytes/text/json`` is the crash-safe write primitive used
by every runtime-state writer in ``.sdd/``. The properties here exist
to catch regressions that would silently corrupt readers under adverse
conditions:

* **Round-trip soundness** across every Hypothesis-generated payload
  (binary, UTF-8 text, JSON values) — readers must always see the
  exact bytes/text/value the writer was handed.

* **No stale tmp slot leakage** — after any successful write the
  parent directory must contain only ``path`` itself; the
  ``.tmp.<pid>.<rand>`` shim file is never visible after replace().

* **Repeat-write convergence** — repeated writes with different
  payloads on the same path always leave the *last* payload visible,
  never an intermediate value. Catches accidental write-then-fsync
  ordering bugs.

* **Concurrent writers do not corrupt readers** — when N threads
  hammer the same path with distinct payloads, a concurrent reader
  always observes a fully-formed previous payload, never a torn
  intermediate. Catches non-atomic rename regressions (e.g. a refactor
  that swaps ``os.replace`` for ``shutil.move``).

Each property uses small max-example budgets so the file completes in
under ~10 s on a GitHub-hosted runner.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.persistence.atomic_write import (
    write_atomic_bytes,
    write_atomic_json,
    write_atomic_text,
)

# JSON payload strategy. We use a recursive strategy with bounded depth
# so Hypothesis explores nested dicts/lists but never blows the stack.
_JSON_PRIMITIVES = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**62), max_value=2**62)
    # NaN/Inf are intentionally excluded — ``json.dumps`` raises on them
    # by default. The behaviour is locked elsewhere; here we exercise
    # the happy path with values that *should* round-trip exactly.
    | st.floats(allow_nan=False, allow_infinity=False, width=64)
    | st.text(min_size=0, max_size=32)
)
_JSON_VALUES = st.recursive(
    _JSON_PRIMITIVES,
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
    max_leaves=8,
)


@given(payload=st.binary(min_size=0, max_size=4096))
def test_bytes_round_trip(tmp_path_factory: pytest.TempPathFactory, payload: bytes) -> None:
    """Round-trip: every byte payload reads back identically.

    Catches regressions where binary mode is silently downgraded to
    text mode (e.g. ``open(path, 'w')`` instead of ``'wb'``). On
    Windows the universal-newline translation alone would corrupt any
    payload containing ``\\n`` or ``\\r`` bytes.
    """
    target = tmp_path_factory.mktemp("aw") / "bytes.bin"
    write_atomic_bytes(target, payload)
    assert target.read_bytes() == payload


@given(text=st.text(min_size=0, max_size=4096))
def test_text_round_trip_utf8(tmp_path_factory: pytest.TempPathFactory, text: str) -> None:
    """Round-trip: every UTF-8 representable string reads back identically.

    Covers RTL marks, combining characters, surrogate-pair-equivalent
    code points (Hypothesis' ``st.text`` excludes lone surrogates by
    default, which matches the encoder's contract).

    Reads via ``read_bytes().decode()`` rather than ``read_text`` so
    Python's universal-newline translation does not silently mask a
    real corruption (``read_text`` rewrites ``\\r`` → ``\\n`` on read).
    """
    target = tmp_path_factory.mktemp("aw") / "text.txt"
    write_atomic_text(target, text)
    assert target.read_bytes().decode("utf-8") == text


@given(payload=_JSON_VALUES)
def test_json_round_trip(tmp_path_factory: pytest.TempPathFactory, payload: Any) -> None:
    """Round-trip: every JSON-serialisable value loads back equal.

    Equality is established by re-loading via ``json.loads`` so that
    incidental int-vs-float drift in JSON (1 == 1.0) is not mistaken
    for a bug. This catches subtle regressions in the indent/sort_keys
    forwarding wrapper.
    """
    target = tmp_path_factory.mktemp("aw") / "payload.json"
    write_atomic_json(target, payload)
    loaded = json.loads(target.read_text())
    assert loaded == payload


@given(payload=st.binary(min_size=0, max_size=512))
def test_no_tmp_files_left_after_write(
    tmp_path_factory: pytest.TempPathFactory,
    payload: bytes,
) -> None:
    """Successful writes never leak ``.tmp.<pid>.<rand>`` siblings.

    Long-lived ``.sdd/runtime/`` directories accumulate gigabytes of
    stale temp files if this invariant breaks. The check is the same
    as in tests/unit/test_atomic_write.py but lifted to property form
    so any unicode/empty/large-payload edge case is also exercised.
    """
    workdir = tmp_path_factory.mktemp("aw")
    target = workdir / "state.bin"
    write_atomic_bytes(target, payload)

    children = sorted(p.name for p in workdir.iterdir())
    assert children == ["state.bin"]


@given(
    payloads=st.lists(st.binary(min_size=0, max_size=128), min_size=2, max_size=8),
)
def test_repeated_writes_converge_to_last(
    tmp_path_factory: pytest.TempPathFactory,
    payloads: list[bytes],
) -> None:
    """After N sequential writes, only the final payload is visible.

    Catches regressions where ``os.replace`` is performed in the wrong
    order vs ``fsync`` (a partial write could otherwise resurrect a
    previous payload on readback). Each example uses a fresh path so
    we don't accumulate side-state between Hypothesis trials.
    """
    target = tmp_path_factory.mktemp("aw") / "state.bin"
    for payload in payloads:
        write_atomic_bytes(target, payload)
    assert target.read_bytes() == payloads[-1]


@settings(
    max_examples=20,
    deadline=None,  # Threads are slow under CI; deadline would flake.
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    payloads=st.lists(st.binary(min_size=1, max_size=64), min_size=2, max_size=4),
)
def test_concurrent_writers_never_corrupt_reader(
    tmp_path_factory: pytest.TempPathFactory,
    payloads: list[bytes],
) -> None:
    """Concurrent writers + a polling reader: reader sees only whole writes.

    Spawns one writer per payload plus one reader thread. The reader
    polls the target and records every distinct payload it observes.
    Every observation must equal one of the inputs verbatim — never
    a truncated, empty, or interleaved snapshot. This catches
    regressions that swap ``os.replace`` for a non-atomic write
    sequence.

    The starting bytes are pre-seeded so the reader's first observation
    is well-defined and the assertion does not race the very first
    write.
    """
    if len(set(payloads)) < 2:
        pytest.skip("payloads insufficiently distinct to exercise contention")

    target = tmp_path_factory.mktemp("aw") / "state.bin"
    # Seed with the first payload so readers always see *something*.
    write_atomic_bytes(target, payloads[0])
    valid_payloads = set(payloads)

    stop = threading.Event()
    observed_corrupt: list[bytes] = []
    barrier = threading.Barrier(len(payloads) + 1)

    def reader() -> None:
        barrier.wait()
        # ~50 polls is enough to catch a torn write with the writer
        # threads running unopposed. Increasing this further only
        # increases CI cost.
        for _ in range(50):
            try:
                snap = target.read_bytes()
            except FileNotFoundError:
                continue
            if snap not in valid_payloads:
                observed_corrupt.append(snap)
                stop.set()
                return
        stop.set()

    def writer(payload: bytes) -> None:
        barrier.wait()
        for _ in range(20):
            write_atomic_bytes(target, payload)
            if stop.is_set():
                return

    threads: list[threading.Thread] = [threading.Thread(target=reader, daemon=True)]
    threads.extend(threading.Thread(target=writer, args=(p,), daemon=True) for p in payloads)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()
    assert not observed_corrupt, f"reader observed torn payload: {observed_corrupt[:1]!r}"


@given(payload=st.binary(min_size=0, max_size=64))
def test_parent_dir_auto_created(
    tmp_path_factory: pytest.TempPathFactory,
    payload: bytes,
) -> None:
    """Writing to a path under a missing parent dir creates the parent.

    Documents the existing public contract:
    ``path.parent.mkdir(parents=True, exist_ok=True)``. A regression
    that removed the call would surface as a silent FileNotFoundError
    in every fresh ``.sdd/runtime/`` writer.
    """
    deep = tmp_path_factory.mktemp("aw") / "a" / "b" / "c" / "state.bin"
    write_atomic_bytes(deep, payload)
    assert deep.read_bytes() == payload


@given(text=st.text(min_size=0, max_size=256))
def test_text_overwrites_larger_existing_file(
    tmp_path_factory: pytest.TempPathFactory,
    text: str,
) -> None:
    """Replacing a larger file with a smaller payload yields the smaller size.

    A regression that wrote to the destination directly (instead of
    via the temp + replace path) might end up appending or leaving
    trailing bytes from the previous payload. This property catches
    that by overwriting a known-large file with a Hypothesis-chosen
    (possibly empty) shorter text.
    """
    target = tmp_path_factory.mktemp("aw") / "state.txt"
    target.write_text("X" * 4096, encoding="utf-8")
    write_atomic_text(target, text)
    assert target.read_bytes().decode("utf-8") == text
