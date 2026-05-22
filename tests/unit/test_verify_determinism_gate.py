"""CLI gate tests for ``bernstein verify --determinism`` with --expect/--baseline.

The bare ``--determinism <run-id>`` path stays observe-only (print + exit 0).
``--expect``/``--baseline`` turn the printed fingerprint into an assertable
gate bound to the WAL hash chain: a mismatch exits non-zero and names the
first diverging WAL entry derived from the existing chain order. These tests
pin both the exit-code contract and the divergence pinpoint.

``verify`` resolves its ``.sdd`` directory relative to the current working
directory, so each test ``chdir``s into a tmp project (``monkeypatch.chdir``
restores cwd afterwards) and writes the run WAL under ``.sdd/runtime/wal/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from bernstein.core.wal import ExecutionFingerprint, WALReader, WALWriter
from click.testing import CliRunner

from bernstein.cli.commands.verify_cmd import verify_cmd

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

Decision = tuple[str, dict[str, Any], dict[str, Any]]


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a wide terminal so Rich does not truncate 64-char digests.

    The shared Rich console reads ``COLUMNS`` dynamically, so a real
    operator on any reasonably-sized terminal sees the full fingerprint;
    the default 80-col pytest fallback would otherwise ellipsize it.
    """
    monkeypatch.setenv("COLUMNS", "200")


def _write_wal(sdd_dir: Path, run_id: str, decisions: Sequence[Decision]) -> None:
    writer = WALWriter(run_id=run_id, sdd_dir=sdd_dir)
    for decision_type, inputs, output in decisions:
        writer.append(decision_type, inputs, output, "actor")


def _fingerprint(sdd_dir: Path, run_id: str) -> str:
    return ExecutionFingerprint.from_wal(WALReader(run_id=run_id, sdd_dir=sdd_dir)).compute()


# Immutable shared fixture: a tuple so a test cannot accidentally mutate the
# decision trace other tests rely on.
_DECISIONS: tuple[Decision, ...] = (
    ("tick_start", {"tick": 1}, {}),
    ("task_claimed", {"task_id": "T-1"}, {"batch_size": 1}),
    ("task_completed", {"task_id": "T-1"}, {"janitor_passed": True}),
)


def test_bare_determinism_prints_and_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: bare --determinism is unchanged (exit 0, fingerprint table)."""
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)
    fp = _fingerprint(sdd, "run-a")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-a"])

    assert result.exit_code == 0
    assert "Execution Determinism Fingerprint" in result.output
    assert fp in result.output
    # No expected/actual comparison block in the bare path.
    assert "Expected" not in result.output


def test_expect_correct_fingerprint_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)
    fp = _fingerprint(sdd, "run-a")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-a", "--expect", fp])

    assert result.exit_code == 0
    assert fp in result.output


def test_expect_wrong_fingerprint_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)
    fp = _fingerprint(sdd, "run-a")
    wrong = "f" * 64

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-a", "--expect", wrong])

    assert result.exit_code == 2
    # Both digests surfaced so the operator can diff them.
    assert wrong in result.output
    assert fp in result.output


def test_baseline_matching_runs_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)
    _write_wal(sdd, "run-b", _DECISIONS)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-b", "--baseline", "run-a"])

    assert result.exit_code == 0


def test_baseline_diverging_runs_exit_two_and_pinpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)
    # run-b forks at index 1 (different task_id input).
    diverged: tuple[Decision, ...] = (
        ("tick_start", {"tick": 1}, {}),
        ("task_claimed", {"task_id": "T-999"}, {"batch_size": 1}),
        ("task_completed", {"task_id": "T-999"}, {"janitor_passed": True}),
    )
    _write_wal(sdd, "run-b", diverged)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-b", "--baseline", "run-a"])

    assert result.exit_code == 2
    # The first diverging WAL entry (seq 1 / index 1) is named, not just the digests.
    assert "task_claimed" in result.output


def test_expect_missing_wal_exits_nonzero_with_existing_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing WAL still fails with the original 'WAL file not found' message."""
    sdd = tmp_path / ".sdd"
    sdd.mkdir()

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "ghost", "--expect", "a" * 64])

    assert result.exit_code != 0
    assert "WAL file not found" in result.output


def test_baseline_missing_baseline_wal_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-b", _DECISIONS)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-b", "--baseline", "ghost"])

    assert result.exit_code != 0
    assert "WAL file not found" in result.output


def test_expect_and_baseline_together_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--expect and --baseline are mutually exclusive; reject rather than guess."""
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)
    _write_wal(sdd, "run-b", _DECISIONS)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        verify_cmd,
        ["--determinism", "run-b", "--baseline", "run-a", "--expect", "a" * 64],
    )

    assert result.exit_code != 0
    # Pin the contract: the rejection names mutual exclusivity, not just a code.
    assert "mutually exclusive" in result.output.lower()


def test_expect_without_determinism_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--expect requires --determinism; using it alone errors clearly."""
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--expect", "a" * 64])

    assert result.exit_code != 0
    # Pin the contract: the rejection explains the --determinism dependency.
    assert "require" in result.output.lower()
    assert "--determinism" in result.output


def test_mismatch_output_scopes_the_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mismatch wording must scope the guarantee to the WAL decision trace."""
    sdd = tmp_path / ".sdd"
    sdd.mkdir()
    _write_wal(sdd, "run-a", _DECISIONS)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(verify_cmd, ["--determinism", "run-a", "--expect", "0" * 64])

    assert result.exit_code == 2
    # A green check proves the WAL decision trace matched, not on-disk artefacts.
    assert "decision trace" in result.output.lower()
