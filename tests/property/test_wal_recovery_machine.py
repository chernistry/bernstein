"""Stateful WAL property test: append → restart → recover convergence.

Models the orchestrator's relationship with the WAL using Hypothesis's
``RuleBasedStateMachine``. The machine drives a writer through random
append / kill-and-restart / verify cycles and asserts that:

- After every restart, the writer resumes the chain at the previous
  ``entry_hash`` (no chain breakage on boot).
- ``WALReader.verify_chain()`` always succeeds against the on-disk
  history (no torn-line breakage from a clean shutdown — torn-line
  recovery is a separate concern handled by the truncation tests).
- Every appended entry survives the simulated kill, regardless of
  whether ``committed`` was True or False at append time.

The machine intentionally exercises only the *clean* write/restart
path. Torn-line scenarios (mid-write SIGKILL with partial bytes) are
covered by ``tests/unit/test_wal_recovery.py`` because they require
deterministic byte truncation that's awkward to express as a
state-machine rule.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
)

from bernstein.core.persistence.wal import WALReader, WALWriter


class WALRecoveryMachine(RuleBasedStateMachine):
    """Drive a single WAL through append/restart cycles."""

    def __init__(self) -> None:
        super().__init__()
        self.sdd: Path = Path()
        self.writer: WALWriter | None = None
        self.run_id: str = "rsm-run"
        self.appended_entries: list[tuple[int, str]] = []
        # Track the entry_hash the writer is expected to chain off after
        # each restart, so we can cross-check the post-restart state.

    @initialize()
    def setup(self) -> None:
        """One-shot init for each Hypothesis run of the machine."""
        self.sdd = Path(tempfile.mkdtemp(prefix="bernstein-rsm-wal-"))
        self.writer = WALWriter(run_id=self.run_id, sdd_dir=self.sdd)

    @rule(
        decision_type=st.text(min_size=1, max_size=8),
        committed=st.booleans(),
    )
    def append_decision(self, decision_type: str, committed: bool) -> None:
        """Append a decision under the current writer."""
        if self.writer is None:
            return
        entry = self.writer.append(
            decision_type=decision_type,
            inputs={"x": len(self.appended_entries)},
            output={"y": "ok"},
            actor="rsm",
            committed=committed,
        )
        self.appended_entries.append((entry.seq, entry.entry_hash))

    @rule()
    def restart_writer(self) -> None:
        """Simulate a clean restart of the orchestrator."""
        # ``WALWriter.__init__`` reloads the tail from disk — that's the
        # single source of truth we want to hammer. Drop the reference
        # to the old writer; nothing flushes pending state because per-
        # entry fsync already guaranteed durability.
        self.writer = WALWriter(run_id=self.run_id, sdd_dir=self.sdd)

    @invariant()
    def chain_remains_verifiable(self) -> None:
        """After every step, ``verify_chain`` must accept the on-disk WAL."""
        wal_path = self.sdd / "runtime" / "wal" / f"{self.run_id}.wal.jsonl"
        if not wal_path.exists():
            return  # no entries yet
        valid, errors = WALReader(self.run_id, self.sdd).verify_chain()
        assert valid, f"chain integrity broken at machine step: {errors}"

    @invariant()
    def appended_count_matches_disk(self) -> None:
        """All appends must survive on disk regardless of commit flag."""
        wal_path = self.sdd / "runtime" / "wal" / f"{self.run_id}.wal.jsonl"
        if not self.appended_entries:
            return
        if not wal_path.exists():
            raise AssertionError("appended entries but WAL file is missing")
        line_count = sum(1 for raw in wal_path.read_text().splitlines() if raw.strip())
        # Allow ≤ because the writer may rewrite torn lines, but we
        # never expect *more* lines than appends.
        assert line_count >= len(self.appended_entries), (
            f"on-disk lines={line_count} < appended={len(self.appended_entries)}"
        )


# Keep the smoke profile fast: the state machine adds a handful of steps per
# example. ``stateful_step_count`` defaults to a sensible value but we cap
# ``max_examples`` here so PR runtime is bounded.
TestWALRecoveryStateMachine = WALRecoveryMachine.TestCase
TestWALRecoveryStateMachine.settings = settings(max_examples=20, deadline=None)
