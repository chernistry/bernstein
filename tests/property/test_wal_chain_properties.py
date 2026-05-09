"""Property tests for the orchestrator Write-Ahead Log.

Two families of properties:

- **Append-then-verify** — random sequences of decisions must produce
  a hash chain that ``WALReader.verify_chain()`` accepts.
- **Tamper detection** — any single-byte mutation of an on-disk WAL
  line must break the chain (``verify_chain`` reports at least one
  error).

State-machine coverage (Hypothesis ``RuleBasedStateMachine``) is in a
companion file (``test_wal_recovery_machine.py``) so this file stays
tightly focused on the chain primitives.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bernstein.core.persistence.wal import WALReader, WALWriter

_ALPHABET = st.characters(min_codepoint=0x20, max_codepoint=0x7E)
_TEXT = st.text(_ALPHABET, min_size=1, max_size=24)
_PAYLOAD = st.dictionaries(
    keys=st.text(_ALPHABET, min_size=1, max_size=8),
    values=st.one_of(_TEXT, st.integers(-1_000, 1_000), st.booleans()),
    max_size=4,
)


def _writer_for(run_id: str) -> tuple[WALWriter, Path]:
    """Return a writer with its own ``.sdd/`` tempdir."""
    sdd = Path(tempfile.mkdtemp(prefix="bernstein-prop-wal-"))
    return WALWriter(run_id=run_id, sdd_dir=sdd), sdd


@given(
    decisions=st.lists(
        st.tuples(_TEXT, _PAYLOAD, _PAYLOAD, _TEXT, st.booleans()),
        min_size=1,
        max_size=12,
    ),
)
def test_chain_extends_with_arbitrary_decisions(
    decisions: list[tuple[str, dict[str, Any], dict[str, Any], str, bool]],
) -> None:
    """Random ``append()`` sequences must verify cleanly."""
    writer, sdd = _writer_for("run-prop-1")
    for decision_type, inputs, output, actor, committed in decisions:
        writer.append(
            decision_type=decision_type,
            inputs=inputs,
            output=output,
            actor=actor,
            committed=committed,
        )

    valid, errors = WALReader("run-prop-1", sdd).verify_chain()
    assert valid, f"chain rejected its own writes: {errors}"


@settings(max_examples=30)
@given(
    decisions=st.lists(
        st.tuples(_TEXT, _PAYLOAD, _PAYLOAD, _TEXT, st.booleans()),
        min_size=2,
        max_size=8,
    ),
    flip_offset=st.integers(min_value=0, max_value=20_000),
)
def test_single_byte_flip_breaks_verify_chain(
    decisions: list[tuple[str, dict[str, Any], dict[str, Any], str, bool]],
    flip_offset: int,
) -> None:
    """Flipping any byte of the on-disk WAL must trip ``verify_chain``."""
    writer, sdd = _writer_for("run-prop-flip")
    for decision_type, inputs, output, actor, committed in decisions:
        writer.append(
            decision_type=decision_type,
            inputs=inputs,
            output=output,
            actor=actor,
            committed=committed,
        )

    wal_path = sdd / "runtime" / "wal" / "run-prop-flip.wal.jsonl"
    raw = wal_path.read_bytes()
    if not raw:
        pytest.skip("WAL produced no bytes")

    pos = flip_offset % len(raw)
    mutated = bytearray(raw)
    mutated[pos] ^= 0x01
    if mutated == raw:
        pytest.skip("XOR with 0x01 unchanged (impossible)")
    wal_path.write_bytes(bytes(mutated))

    reader = WALReader("run-prop-flip", sdd)
    valid, errors = reader.verify_chain()
    assert not valid, "WAL byte flip went undetected"
    assert errors


@given(
    n=st.integers(min_value=2, max_value=10),
    actor=_TEXT,
)
def test_seq_is_strictly_monotonic(n: int, actor: str) -> None:
    """Sequence numbers must increment by exactly one per append."""
    writer, _ = _writer_for("run-seq")
    seqs: list[int] = []
    for i in range(n):
        entry = writer.append(
            decision_type="t",
            inputs={"i": i},
            output={"r": i},
            actor=actor,
        )
        seqs.append(entry.seq)

    assert seqs == list(range(seqs[0], seqs[0] + n))
