"""Tests for the audit-slice extractor.

Slice semantics: ``--from`` and ``--to`` name the HMAC of an event already
recorded in the log; both bounds are inclusive.  The slice must remain
chain-contiguous after extraction and must hash deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bernstein.core.audit import AuditLog
from click.testing import CliRunner

from bernstein.core.security.audit_slice import (
    AuditSliceError,
    slice_audit_log,
    verify_slice_chain,
    write_slice_jsonl,
)


def _build_log(audit_dir: Path, count: int = 5) -> list[str]:
    """Seed an audit log with ``count`` events.  Returns each event's HMAC."""
    log = AuditLog(audit_dir, key=b"test-key")
    return [log.log("evt", "actor", "task", f"id-{i}", {"i": i}).hmac for i in range(count)]


# ---------------------------------------------------------------------------
# slice_audit_log core API
# ---------------------------------------------------------------------------


def test_slice_full_range_returns_all_events(tmp_path: Path) -> None:
    """No bounds = whole chain."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 4)

    result = slice_audit_log(audit_dir)

    assert result.event_count == 4
    assert [e["hmac"] for e in result.events] == hmacs
    assert result.from_hmac is None
    assert result.to_hmac is None


def test_slice_with_from_and_to_inclusive(tmp_path: Path) -> None:
    """Both bounds inclusive - slice covers from..to events."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 5)

    result = slice_audit_log(audit_dir, from_hmac=hmacs[1], to_hmac=hmacs[3])

    assert [e["hmac"] for e in result.events] == hmacs[1:4]
    assert result.event_count == 3


def test_slice_single_event_when_from_equals_to(tmp_path: Path) -> None:
    """``from == to`` yields exactly that event."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 3)

    result = slice_audit_log(audit_dir, from_hmac=hmacs[1], to_hmac=hmacs[1])

    assert result.event_count == 1
    assert result.events[0]["hmac"] == hmacs[1]


def test_slice_to_only_yields_head(tmp_path: Path) -> None:
    """Only ``--to`` set: include genesis through that event."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 4)

    result = slice_audit_log(audit_dir, to_hmac=hmacs[2])

    assert [e["hmac"] for e in result.events] == hmacs[:3]


def test_slice_from_only_yields_tail(tmp_path: Path) -> None:
    """Only ``--from`` set: include from that event through end."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 4)

    result = slice_audit_log(audit_dir, from_hmac=hmacs[2])

    assert [e["hmac"] for e in result.events] == hmacs[2:]


def test_slice_unknown_from_hash_raises(tmp_path: Path) -> None:
    """Unknown ``--from`` hash → AuditSliceError."""
    audit_dir = tmp_path / "audit"
    _build_log(audit_dir, 3)

    with pytest.raises(AuditSliceError, match="--from hash not found"):
        slice_audit_log(audit_dir, from_hmac="0" * 64)


def test_slice_unknown_to_hash_raises(tmp_path: Path) -> None:
    """Unknown ``--to`` hash → AuditSliceError."""
    audit_dir = tmp_path / "audit"
    _build_log(audit_dir, 3)

    with pytest.raises(AuditSliceError, match="--to hash not found"):
        slice_audit_log(audit_dir, to_hmac="f" * 64)


def test_slice_from_after_to_raises(tmp_path: Path) -> None:
    """``--from`` later in chain than ``--to`` → AuditSliceError."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 4)

    with pytest.raises(AuditSliceError, match="must precede"):
        slice_audit_log(audit_dir, from_hmac=hmacs[3], to_hmac=hmacs[1])


def test_slice_missing_audit_dir_raises(tmp_path: Path) -> None:
    """No audit directory → AuditSliceError."""
    with pytest.raises(AuditSliceError, match="not found"):
        slice_audit_log(tmp_path / "nope")


def test_slice_empty_audit_dir_raises(tmp_path: Path) -> None:
    """Audit directory exists but no JSONL files → AuditSliceError."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()

    with pytest.raises(AuditSliceError, match="empty"):
        slice_audit_log(audit_dir)


# ---------------------------------------------------------------------------
# verify_slice_chain
# ---------------------------------------------------------------------------


def test_slice_chain_verifies(tmp_path: Path) -> None:
    """Slice from a healthy log must self-verify (prev_hmac linkage)."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 4)

    result = slice_audit_log(audit_dir, from_hmac=hmacs[1], to_hmac=hmacs[3])

    valid, errors = verify_slice_chain(result)
    assert valid is True
    assert errors == []


# ---------------------------------------------------------------------------
# write_slice_jsonl - determinism guarantees
# ---------------------------------------------------------------------------


def test_slice_jsonl_is_byte_deterministic(tmp_path: Path) -> None:
    """Two writes of the same slice produce byte-identical files."""
    audit_dir = tmp_path / "audit"
    _build_log(audit_dir, 3)

    result = slice_audit_log(audit_dir)
    out1 = write_slice_jsonl(result, tmp_path / "a.jsonl")
    out2 = write_slice_jsonl(result, tmp_path / "b.jsonl")

    assert out1.read_bytes() == out2.read_bytes()


def test_slice_jsonl_contains_expected_events(tmp_path: Path) -> None:
    """Output JSONL round-trips back to the source events."""
    audit_dir = tmp_path / "audit"
    hmacs = _build_log(audit_dir, 5)

    result = slice_audit_log(audit_dir, from_hmac=hmacs[0], to_hmac=hmacs[2])
    out_path = write_slice_jsonl(result, tmp_path / "slice.jsonl")

    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [e["hmac"] for e in parsed] == hmacs[:3]


def test_slice_jsonl_keys_are_sorted(tmp_path: Path) -> None:
    """Each line's keys are in canonical (sorted) order - required for hashing."""
    audit_dir = tmp_path / "audit"
    _build_log(audit_dir, 1)

    result = slice_audit_log(audit_dir)
    out_path = write_slice_jsonl(result, tmp_path / "slice.jsonl")

    line = out_path.read_text(encoding="utf-8").splitlines()[0]
    # Expect "actor" before "details" before "event_type" - confirms sort_keys.
    actor_pos = line.index('"actor"')
    details_pos = line.index('"details"')
    event_type_pos = line.index('"event_type"')
    assert actor_pos < details_pos < event_type_pos


# ---------------------------------------------------------------------------
# CLI surface - `bernstein audit slice`
# ---------------------------------------------------------------------------


def _audit_dir_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a log at ``<tmp>/.sdd/audit`` and chdir there for the CLI."""
    monkeypatch.chdir(tmp_path)
    audit_dir = tmp_path / ".sdd" / "audit"
    audit_dir.mkdir(parents=True)
    return audit_dir


def test_cli_slice_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`bernstein audit slice` happy path - exits 0 and writes the events."""
    audit_dir = _audit_dir_in_cwd(tmp_path, monkeypatch)
    hmacs = _build_log(audit_dir, 4)

    from bernstein.cli.commands.audit_cmd import audit_group

    out_path = tmp_path / "out.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        ["slice", "--from", hmacs[1], "--to", hmacs[2], "-o", str(out_path)],
    )

    assert result.exit_code == 0, result.output
    assert out_path.is_file()
    parsed = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert [e["hmac"] for e in parsed] == hmacs[1:3]


def test_cli_slice_unknown_hash_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown HMAC bound aborts with a non-zero exit and a helpful message."""
    audit_dir = _audit_dir_in_cwd(tmp_path, monkeypatch)
    _build_log(audit_dir, 2)

    from bernstein.cli.commands.audit_cmd import audit_group

    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        ["slice", "--from", "0" * 64, "-o", str(tmp_path / "out.jsonl")],
    )

    assert result.exit_code == 1
    assert "--from hash not found" in result.output
