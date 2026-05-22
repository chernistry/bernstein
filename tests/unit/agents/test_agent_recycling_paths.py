"""Behavioral tests for ``agent_recycling`` idle/kill/loop recovery.

Drives the recycling helpers with a duck-typed orchestrator fake and
real ``AgentSession`` objects. The reaping side effects (partial-work
save, abort propagation, metrics collector, worktree cleanup) are
stubbed so the control-flow branches - first-detection vs grace-elapsed
recycle, completion-marker fast reap, kill-signal processing, staggered
shutdown, and loop/deadlock recovery - run end to end and their
observable effects (kill calls, signal writes, file removal, list
mutations) are asserted.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bernstein.core.models import (
    AgentSession,
    Complexity,
    ModelConfig,
    Scope,
    Task,
    TaskStatus,
    TaskType,
)

from bernstein.core.agents import agent_recycling as ar
from bernstein.core.agents.agent_recycling import (
    _IDLE_GRACE_S,
    _build_snapshot_indexes,
    _detect_idle_reason,
    check_kill_signals,
    check_loops_and_deadlocks,
    recycle_idle_agents,
    send_shutdown_signals,
)


def _session(sid: str = "A-1", *, task_ids: list[str] | None = None, status: str = "working") -> AgentSession:
    return AgentSession(
        id=sid,
        role="backend",
        task_ids=["T-1"] if task_ids is None else task_ids,
        status=status,
        spawn_ts=100.0,
        model_config=ModelConfig("sonnet", "high"),
    )


def _task(tid: str, role: str = "backend") -> Task:
    return Task(
        id=tid,
        title="t",
        description="d",
        role=role,
        scope=Scope.MEDIUM,
        complexity=Complexity.MEDIUM,
        status=TaskStatus.OPEN,
        task_type=TaskType.STANDARD,
        priority=2,
        owned_files=[],
        mcp_servers=[],
    )


# ---------------------------------------------------------------------------
# _build_snapshot_indexes
# ---------------------------------------------------------------------------


def test_build_snapshot_indexes_buckets_correctly() -> None:
    """Resolved ids span done/failed/blocked; per-role counts split open vs active."""
    snapshot = {
        "done": [_task("T1")],
        "failed": [_task("T2")],
        "blocked": [_task("T3")],
        "open": [_task("T4", "backend"), _task("T5", "qa")],
        "claimed": [_task("T6", "backend")],
        "in_progress": [_task("T7", "qa")],
    }
    resolved, open_per_role, active_per_role = _build_snapshot_indexes(snapshot)
    assert resolved == {"T1", "T2", "T3"}
    assert open_per_role == {"backend": 1, "qa": 1}
    # active = open + claimed + in_progress
    assert active_per_role == {"backend": 2, "qa": 2}


def test_build_snapshot_indexes_empty_snapshot() -> None:
    """An empty snapshot yields empty indexes."""
    resolved, open_per_role, active_per_role = _build_snapshot_indexes({})
    assert resolved == set()
    assert open_per_role == {}
    assert active_per_role == {}


# ---------------------------------------------------------------------------
# _detect_idle_reason (four cases + none)
# ---------------------------------------------------------------------------


def _orch_for_detect(hb_ts: float | None) -> SimpleNamespace:
    orch = SimpleNamespace(_signal_mgr=MagicMock())
    orch._signal_mgr.read_heartbeat.return_value = None if hb_ts is None else SimpleNamespace(timestamp=hb_ts)
    return orch


def test_detect_idle_reason_all_tasks_resolved() -> None:
    """An agent whose tasks are all resolved is idle."""
    orch = _orch_for_detect(None)
    reason = _detect_idle_reason(orch, _session(task_ids=["T1", "T2"]), 1000.0, 300.0, {"T1", "T2"}, {}, {"backend": 1})
    assert reason == "task_already_resolved"


def test_detect_idle_reason_stale_heartbeat() -> None:
    """A heartbeat older than the idle threshold (and no live PID) is idle."""
    orch = _orch_for_detect(hb_ts=600.0)  # age 400 > 300
    reason = _detect_idle_reason(orch, _session(task_ids=["TX"]), 1000.0, 300.0, set(), {"backend": 1}, {"backend": 1})
    assert reason == "no_heartbeat_300s"


def test_detect_idle_reason_role_queue_empty_no_tasks() -> None:
    """An agent with no tasks and an empty role queue is idle."""
    orch = _orch_for_detect(None)
    reason = _detect_idle_reason(orch, _session(task_ids=[]), 1000.0, 300.0, set(), {"backend": 0}, {"backend": 1})
    assert reason == "role_queue_empty_no_tasks"


def test_detect_idle_reason_role_drained_rebalance() -> None:
    """An agent in a fully drained role is idle for rebalancing."""
    orch = _orch_for_detect(None)
    reason = _detect_idle_reason(orch, _session(task_ids=["TY"]), 1000.0, 300.0, set(), {"backend": 1}, {"backend": 0})
    assert reason == "role_drained_rebalance"


def test_detect_idle_reason_active_agent_is_not_idle() -> None:
    """A fresh-heartbeat agent with active work is not idle."""
    orch = _orch_for_detect(hb_ts=999.0)  # age 1s, well within threshold
    reason = _detect_idle_reason(orch, _session(task_ids=["TZ"]), 1000.0, 300.0, set(), {"backend": 1}, {"backend": 1})
    assert reason is None


# ---------------------------------------------------------------------------
# _recycle_or_kill (first detection / within grace / after grace)
# ---------------------------------------------------------------------------


def test_recycle_or_kill_first_detection_sends_shutdown() -> None:
    """First idle detection writes a SHUTDOWN and records the timestamp."""
    session = _session()
    orch = SimpleNamespace(_signal_mgr=MagicMock(), _idle_shutdown_ts={}, _spawner=MagicMock())
    ar._recycle_or_kill(orch, session, now=1000.0, reason="role_drained")
    orch._signal_mgr.write_shutdown.assert_called_once()
    assert orch._idle_shutdown_ts["A-1"] == 1000.0
    orch._spawner.kill.assert_not_called()


def test_recycle_or_kill_within_grace_does_not_kill() -> None:
    """A repeat check inside the grace window does not force-kill."""
    session = _session()
    orch = SimpleNamespace(_signal_mgr=MagicMock(), _idle_shutdown_ts={"A-1": 1000.0}, _spawner=MagicMock())
    ar._recycle_or_kill(orch, session, now=1000.0 + _IDLE_GRACE_S - 1, reason="role_drained")
    orch._spawner.kill.assert_not_called()


def test_recycle_or_kill_after_grace_force_kills() -> None:
    """Once the grace window elapses the agent is force-killed and untracked."""
    session = _session()
    orch = SimpleNamespace(
        _signal_mgr=MagicMock(),
        _idle_shutdown_ts={"A-1": 1000.0},
        _spawner=MagicMock(),
    )
    with (
        patch.object(ar, "_save_partial_work"),
        patch.object(ar, "_propagate_abort_to_children"),
        patch("bernstein.core.metrics.get_collector", return_value=MagicMock()),
    ):
        ar._recycle_or_kill(orch, session, now=1000.0 + _IDLE_GRACE_S + 1, reason="role_drained")
    orch._spawner.kill.assert_called_once()
    assert "A-1" not in orch._idle_shutdown_ts


# ---------------------------------------------------------------------------
# recycle_idle_agents
# ---------------------------------------------------------------------------


def test_recycle_idle_agents_completion_marker_reaps(tmp_path: Path) -> None:
    """An agent with a completion marker is reaped immediately and the marker removed."""
    completed = tmp_path / ".sdd" / "runtime" / "completed"
    completed.mkdir(parents=True)
    (completed / "A-1").write_text("")
    session = _session("A-1")
    spawner = MagicMock()
    spawner.check_alive.return_value = True
    orch = SimpleNamespace(
        _agents={"A-1": session},
        _workdir=tmp_path,
        _spawner=spawner,
        _signal_mgr=MagicMock(),
        _idle_shutdown_ts={},
        _config=SimpleNamespace(evolve_mode=False),
    )
    with (
        patch.object(ar, "_save_partial_work"),
        patch.object(ar, "_propagate_abort_to_children"),
        patch("bernstein.core.metrics.get_collector", return_value=MagicMock()),
    ):
        recycle_idle_agents(orch, {})
    spawner.kill.assert_called_once()
    assert not (completed / "A-1").exists()


def test_recycle_idle_agents_skips_not_alive(tmp_path: Path) -> None:
    """Agents whose process is not alive are skipped (no recycle)."""
    (tmp_path / ".sdd" / "runtime" / "completed").mkdir(parents=True)
    session = _session("A-2")
    spawner = MagicMock()
    spawner.check_alive.return_value = False
    orch = SimpleNamespace(
        _agents={"A-2": session},
        _workdir=tmp_path,
        _spawner=spawner,
        _signal_mgr=MagicMock(),
        _idle_shutdown_ts={},
        _config=SimpleNamespace(evolve_mode=False),
    )
    recycle_idle_agents(orch, {})
    spawner.kill.assert_not_called()


# ---------------------------------------------------------------------------
# check_kill_signals
# ---------------------------------------------------------------------------


def test_check_kill_signals_kills_and_removes_file(tmp_path: Path) -> None:
    """A .kill file kills its session, removes the file, and records the reap."""
    runtime = tmp_path / ".sdd" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "A-1.kill").write_text("")
    (runtime / "GHOST.kill").write_text("")  # no matching session
    session = _session("A-1")
    orch = SimpleNamespace(_workdir=tmp_path, _agents={"A-1": session}, _spawner=MagicMock())
    result = SimpleNamespace(reaped=[])
    with patch.object(ar, "_propagate_abort_to_children"):
        check_kill_signals(orch, result)
    orch._spawner.kill.assert_called_once()
    assert not (runtime / "A-1.kill").exists()
    assert not (runtime / "GHOST.kill").exists()  # removed even with no session
    assert result.reaped == ["A-1"]


def test_check_kill_signals_no_runtime_dir_is_noop(tmp_path: Path) -> None:
    """With no runtime dir nothing is reaped."""
    orch = SimpleNamespace(_workdir=tmp_path, _agents={}, _spawner=MagicMock())
    result = SimpleNamespace(reaped=[])
    check_kill_signals(orch, result)
    assert result.reaped == []
    orch._spawner.kill.assert_not_called()


# ---------------------------------------------------------------------------
# send_shutdown_signals
# ---------------------------------------------------------------------------


def test_send_shutdown_signals_skips_dead() -> None:
    """Shutdown signals are written for live agents only."""
    orch = SimpleNamespace(
        _agents={"A-1": _session("A-1"), "A-2": _session("A-2", status="dead")},
        _signal_mgr=MagicMock(),
    )
    send_shutdown_signals(orch, reason="stop")
    assert orch._signal_mgr.write_shutdown.call_count == 1


def test_send_shutdown_signals_staggers_between_agents() -> None:
    """A positive stagger delay sleeps between consecutive shutdowns."""
    orch = SimpleNamespace(
        _agents={"A-1": _session("A-1"), "A-2": _session("A-2")},
        _signal_mgr=MagicMock(),
    )
    with patch.object(ar.time, "sleep") as sleep_spy:
        send_shutdown_signals(orch, reason="drain", stagger_delay_s=0.5)
    # Two agents -> exactly one inter-agent sleep.
    assert sleep_spy.call_count == 1
    sleep_spy.assert_called_once_with(0.5)


def test_send_shutdown_signals_no_stagger_no_sleep() -> None:
    """A zero stagger delay never sleeps."""
    orch = SimpleNamespace(
        _agents={"A-1": _session("A-1"), "A-2": _session("A-2")},
        _signal_mgr=MagicMock(),
    )
    with patch.object(ar.time, "sleep") as sleep_spy:
        send_shutdown_signals(orch, reason="stop", stagger_delay_s=0.0)
    sleep_spy.assert_not_called()


# ---------------------------------------------------------------------------
# check_loops_and_deadlocks
# ---------------------------------------------------------------------------


def test_check_loops_no_detector_is_noop() -> None:
    """Without a loop detector the function is a safe no-op."""
    orch = SimpleNamespace()
    # No exception; nothing to assert beyond completing without a detector.
    check_loops_and_deadlocks(orch)


def test_check_loops_recovers_edit_loop(tmp_path: Path) -> None:
    """A detected edit loop kills the agent and releases its locks."""
    loop = SimpleNamespace(agent_id="A-1", file_path="src/a.py", edit_count=10, window_seconds=60.0)
    detector = MagicMock()
    detector.detect_loops.return_value = [loop]
    detector.detect_deadlocks.return_value = []
    lock_mgr = MagicMock()
    lock_mgr.all_locks.return_value = []
    orch = SimpleNamespace(
        _loop_detector=detector,
        _lock_manager=lock_mgr,
        _agents={"A-1": _session("A-1")},
        _workdir=tmp_path,
        _spawner=MagicMock(),
    )
    with patch.object(ar, "_propagate_abort_to_children"):
        check_loops_and_deadlocks(orch)
    orch._spawner.kill.assert_called_once()
    lock_mgr.release.assert_called_with("A-1")
    detector.clear_wait.assert_called_with("A-1")


def test_check_loops_recovers_deadlock_victim(tmp_path: Path) -> None:
    """A detected deadlock releases the victim agent's locks."""
    deadlock = SimpleNamespace(description="cycle A->B", victim_agent_id="A-9")
    detector = MagicMock()
    detector.detect_loops.return_value = []
    detector.detect_deadlocks.return_value = [deadlock]
    lock_mgr = MagicMock()
    lock_mgr.all_locks.return_value = []
    orch = SimpleNamespace(
        _loop_detector=detector,
        _lock_manager=lock_mgr,
        _agents={},
        _workdir=tmp_path,
        _spawner=MagicMock(),
    )
    check_loops_and_deadlocks(orch)
    lock_mgr.release.assert_called_with("A-9")
    detector.clear_wait.assert_called_with("A-9")
