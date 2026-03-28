"""Tests for idle agent detection and recycling (#333g).

Covers:
- recycle_idle_agents sends SHUTDOWN when agent's task is already resolved
- After grace period, idle agent is SIGKILL'd
- No-heartbeat idle detection (90s normal, 60s in evolve mode)
- Agents with active tasks are left alone
- _idle_shutdown_ts is cleaned up after kill
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bernstein.core.agent_lifecycle import (
    _IDLE_GRACE_S,
    _IDLE_HEARTBEAT_THRESHOLD_EVOLVE_S,
    _IDLE_HEARTBEAT_THRESHOLD_S,
    recycle_idle_agents,
)
from bernstein.core.models import (
    AgentHeartbeat,
    AgentSession,
    Complexity,
    OrchestratorConfig,
    Scope,
    Task,
    TaskStatus,
    TaskType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    id: str = "T-001",
    status: str = "done",
) -> Task:
    return Task(
        id=id,
        title="Test task",
        description="desc",
        role="backend",
        scope=Scope.SMALL,
        complexity=Complexity.LOW,
        status=TaskStatus(status),
        task_type=TaskType.STANDARD,
    )


def _make_session(task_ids: list[str], session_id: str = "s-idle-01") -> AgentSession:
    session = AgentSession(id=session_id, role="backend", pid=12345, task_ids=task_ids)
    session.status = "working"
    return session


def _make_orch(tmp_path: Path, *, evolve_mode: bool = False) -> MagicMock:
    """Build a minimal orchestrator-like object for testing recycle_idle_agents."""
    orch = MagicMock()
    orch._config = OrchestratorConfig(
        evolve_mode=evolve_mode,
        evolution_enabled=False,
    )
    orch._agents = {}
    orch._idle_shutdown_ts = {}

    signal_mgr = MagicMock()
    signal_mgr.read_heartbeat.return_value = None  # no heartbeat by default
    orch._signal_mgr = signal_mgr

    # spawner.check_alive → True by default (process is still running)
    orch._spawner.check_alive.return_value = True

    return orch


# ---------------------------------------------------------------------------
# Task already resolved — SHUTDOWN sent immediately
# ---------------------------------------------------------------------------


def test_shutdown_sent_when_task_already_done(tmp_path: Path) -> None:
    """SHUTDOWN signal must be written the first time an idle agent is detected."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-done-1"])
    orch._agents["s-idle-01"] = session

    tasks_snapshot = {
        "done": [_make_task(id="T-done-1", status="done")],
        "failed": [],
        "open": [],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_called_once()
    call_kwargs = orch._signal_mgr.write_shutdown.call_args
    assert call_kwargs.args[0] == "s-idle-01"
    assert "task_already_resolved" in call_kwargs.kwargs["reason"]
    # Timestamp recorded
    assert "s-idle-01" in orch._idle_shutdown_ts


def test_shutdown_sent_when_task_already_failed(tmp_path: Path) -> None:
    """SHUTDOWN is sent when task status is 'failed'."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-fail-1"])
    orch._agents["s-fail-01"] = session

    tasks_snapshot = {
        "done": [],
        "failed": [_make_task(id="T-fail-1", status="failed")],
        "open": [],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# Grace period elapsed — force kill
# ---------------------------------------------------------------------------


def test_force_kill_after_grace_period(tmp_path: Path) -> None:
    """Agent must be SIGKILL'd once SHUTDOWN was sent > 30s ago."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-done-2"], session_id="s-idle-02")
    orch._agents["s-idle-02"] = session

    # Simulate SHUTDOWN sent 31 seconds ago
    past_ts = time.time() - (_IDLE_GRACE_S + 1)
    orch._idle_shutdown_ts["s-idle-02"] = past_ts

    tasks_snapshot = {
        "done": [_make_task(id="T-done-2", status="done")],
        "failed": [],
        "open": [],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    # SIGKILL must have been called
    orch._spawner.kill.assert_called_once_with(session)
    # SHUTDOWN must NOT be written again (kill path only)
    orch._signal_mgr.write_shutdown.assert_not_called()
    # Tracking entry must be cleared
    assert "s-idle-02" not in orch._idle_shutdown_ts
    # Signal files must be cleared
    orch._signal_mgr.clear_signals.assert_called_once_with("s-idle-02")


def test_no_kill_before_grace_period(tmp_path: Path) -> None:
    """Agent must NOT be killed if grace period has not elapsed yet."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-done-3"], session_id="s-idle-03")
    orch._agents["s-idle-03"] = session

    # SHUTDOWN sent only 5s ago — still within grace window
    orch._idle_shutdown_ts["s-idle-03"] = time.time() - 5.0

    tasks_snapshot = {
        "done": [_make_task(id="T-done-3", status="done")],
        "failed": [],
        "open": [],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._spawner.kill.assert_not_called()
    orch._signal_mgr.write_shutdown.assert_not_called()


# ---------------------------------------------------------------------------
# Active task — agent must not be recycled
# ---------------------------------------------------------------------------


def test_active_agent_not_recycled(tmp_path: Path) -> None:
    """Agents working on open/claimed tasks must not receive SHUTDOWN."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-open-1"])
    orch._agents["s-active-01"] = session

    tasks_snapshot = {
        "done": [],
        "failed": [],
        "open": [_make_task(id="T-open-1", status="open")],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_not_called()
    orch._spawner.kill.assert_not_called()


# ---------------------------------------------------------------------------
# Dead agent — skip
# ---------------------------------------------------------------------------


def test_dead_agent_skipped(tmp_path: Path) -> None:
    """Agents already marked dead must be skipped entirely."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-done-9"])
    session.status = "dead"
    orch._agents["s-dead-01"] = session

    tasks_snapshot = {
        "done": [_make_task(id="T-done-9", status="done")],
        "failed": [],
        "open": [],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_not_called()
    orch._spawner.kill.assert_not_called()


# ---------------------------------------------------------------------------
# Heartbeat-idle detection
# ---------------------------------------------------------------------------


def test_shutdown_sent_on_heartbeat_idle(tmp_path: Path) -> None:
    """SHUTDOWN must be sent when heartbeat is older than idle threshold."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-hb-1"])
    orch._agents["s-hb-01"] = session

    stale_ts = time.time() - (_IDLE_HEARTBEAT_THRESHOLD_S + 1)
    stale_hb = AgentHeartbeat(timestamp=stale_ts)
    orch._signal_mgr.read_heartbeat.return_value = stale_hb

    # Task is still open — heartbeat idle is the trigger
    tasks_snapshot = {
        "done": [],
        "failed": [],
        "open": [_make_task(id="T-hb-1", status="open")],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_called_once()
    call_args = orch._signal_mgr.write_shutdown.call_args
    assert "no_heartbeat_" in call_args.kwargs["reason"]


def test_heartbeat_idle_threshold_lower_in_evolve_mode(tmp_path: Path) -> None:
    """In evolve mode the heartbeat idle threshold drops to 60s."""
    orch = _make_orch(tmp_path, evolve_mode=True)
    session = _make_session(["T-evolve-1"])
    orch._agents["s-ev-01"] = session

    # Heartbeat is 65s stale — above evolve threshold (60s) but below normal (90s)
    stale_ts = time.time() - (_IDLE_HEARTBEAT_THRESHOLD_EVOLVE_S + 5)
    orch._signal_mgr.read_heartbeat.return_value = AgentHeartbeat(timestamp=stale_ts)

    tasks_snapshot = {
        "done": [],
        "failed": [],
        "open": [_make_task(id="T-evolve-1", status="open")],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_called_once()


def test_fresh_heartbeat_agent_not_recycled(tmp_path: Path) -> None:
    """Agent with a recent heartbeat must not be touched."""
    orch = _make_orch(tmp_path)
    session = _make_session(["T-fresh-1"])
    orch._agents["s-fresh-01"] = session

    fresh_ts = time.time() - 10.0  # only 10s old
    orch._signal_mgr.read_heartbeat.return_value = AgentHeartbeat(timestamp=fresh_ts)

    tasks_snapshot = {
        "done": [],
        "failed": [],
        "open": [_make_task(id="T-fresh-1", status="open")],
        "claimed": [],
        "blocked": [],
    }

    recycle_idle_agents(orch, tasks_snapshot)

    orch._signal_mgr.write_shutdown.assert_not_called()
    orch._spawner.kill.assert_not_called()


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


def test_idle_constants_sensible() -> None:
    assert _IDLE_GRACE_S == 30.0
    assert _IDLE_HEARTBEAT_THRESHOLD_S == 90.0
    assert _IDLE_HEARTBEAT_THRESHOLD_EVOLVE_S == 60.0
    assert _IDLE_HEARTBEAT_THRESHOLD_EVOLVE_S < _IDLE_HEARTBEAT_THRESHOLD_S
