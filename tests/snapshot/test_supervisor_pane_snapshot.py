"""Snapshot test: supervisor TUI pane render output.

The supervisor pane displays one line per stalled / parked worker. The
render is deterministic over the sorted ``session_id`` order so a
snapshot covers the byte-stable wire form (Rich markup string).

Update workflow: when a deliberate layout change lands, run
``uv run pytest tests/snapshot/test_supervisor_pane_snapshot.py
--snapshot-update`` and commit the updated ``.ambr``.
"""

from __future__ import annotations

from syrupy.assertion import SnapshotAssertion

from bernstein.tui.status_bar import SupervisorPaneRow, render_supervisor_pane


def _row(
    *,
    worker_id: str,
    session_id: str,
    role: str = "backend",
    stall_reason: str = "heartbeat_stale",
    recommended_action: str = "respawn",
    respawn_budget_remaining: int = 2,
    last_heartbeat_age_s: float | None = 65.0,
    is_stuck: bool = True,
) -> SupervisorPaneRow:
    return SupervisorPaneRow(
        worker_id=worker_id,
        session_id=session_id,
        role=role,
        stall_reason=stall_reason,
        recommended_action=recommended_action,
        respawn_budget_remaining=respawn_budget_remaining,
        last_heartbeat_age_s=last_heartbeat_age_s,
        is_stuck=is_stuck,
    )


def test_supervisor_pane_renders_nothing_when_all_healthy(snapshot: SnapshotAssertion) -> None:
    """No output when no row is stuck - keeps the dashboard compact."""
    rows = [
        _row(worker_id="abc123", session_id="sess-1", is_stuck=False),
        _row(worker_id="def456", session_id="sess-2", is_stuck=False),
    ]
    assert render_supervisor_pane(rows) == snapshot


def test_supervisor_pane_renders_stuck_rows_sorted(snapshot: SnapshotAssertion) -> None:
    """Stuck rows are sorted by session id so the snapshot is byte-stable."""
    rows = [
        _row(
            worker_id="zzz999",
            session_id="sess-z",
            role="manager",
            stall_reason="manager_no_children",
            recommended_action="escalate",
            respawn_budget_remaining=0,
            last_heartbeat_age_s=120.0,
        ),
        _row(
            worker_id="aaa111",
            session_id="sess-a",
            role="backend",
            stall_reason="respawn_budget_exhausted",
            recommended_action="park",
            respawn_budget_remaining=0,
            last_heartbeat_age_s=None,
        ),
        _row(
            worker_id="mmm222",
            session_id="sess-m",
            role="qa",
            stall_reason="heartbeat_stale",
            recommended_action="respawn",
            respawn_budget_remaining=2,
            last_heartbeat_age_s=180.0,
        ),
    ]
    assert render_supervisor_pane(rows) == snapshot
