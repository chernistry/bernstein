"""Unit tests for adaptive stall detection."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from bernstein.core.agent_log_aggregator import AgentLogSummary
from bernstein.core.heartbeat import HeartbeatStatus, check_stalled_tasks, compute_stall_profile
from bernstein.core.models import AgentSession, ModelConfig, ProgressSnapshot, Scope


def _summary(*, rate_limit_hits: int = 0, last_activity_line: int = 1) -> AgentLogSummary:
    return AgentLogSummary(
        session_id="A-1",
        total_lines=1,
        events=[],
        error_count=0,
        warning_count=0,
        files_modified=[],
        tests_run=False,
        tests_passed=False,
        test_summary="",
        rate_limit_hits=rate_limit_hits,
        compile_errors=0,
        tool_failures=0,
        first_meaningful_action_line=0,
        last_activity_line=last_activity_line,
        dominant_failure_category=None,
    )


def test_default_profile(make_task: Any) -> None:
    profile = compute_stall_profile(make_task(), None, None)

    assert (profile.wakeup_threshold, profile.shutdown_threshold, profile.kill_threshold) == (3, 5, 7)


def test_large_task_profile(make_task: Any) -> None:
    profile = compute_stall_profile(make_task(scope=Scope.LARGE), None, None)

    assert (profile.wakeup_threshold, profile.shutdown_threshold, profile.kill_threshold) == (5, 8, 12)


def test_running_tests_profile(make_task: Any) -> None:
    profile = compute_stall_profile(
        make_task(),
        HeartbeatStatus(
            session_id="A-1",
            last_heartbeat=None,
            age_seconds=10.0,
            phase="testing",
            progress_pct=80,
            is_alive=True,
            is_stale=False,
        ),
        None,
    )

    assert (profile.wakeup_threshold, profile.shutdown_threshold, profile.kill_threshold) == (8, 12, 16)


def test_rate_limited_profile(make_task: Any) -> None:
    profile = compute_stall_profile(make_task(), None, _summary(rate_limit_hits=2))

    assert (profile.wakeup_threshold, profile.shutdown_threshold, profile.kill_threshold) == (6, 10, 14)


def test_dead_agent_profile(make_task: Any) -> None:
    profile = compute_stall_profile(
        make_task(),
        HeartbeatStatus(
            session_id="A-1",
            last_heartbeat=None,
            age_seconds=180.0,
            phase="",
            progress_pct=0,
            is_alive=False,
            is_stale=False,
        ),
        _summary(last_activity_line=0),
    )

    assert (profile.wakeup_threshold, profile.shutdown_threshold, profile.kill_threshold) == (2, 3, 5)


def test_heartbeat_overrides_snapshot(tmp_path: Path, make_task: Any) -> None:
    session = AgentSession(
        id="A-1",
        role="backend",
        task_ids=["T-1"],
        status="working",
        spawn_ts=time.time() - 300,
        model_config=ModelConfig("sonnet", "high"),
    )
    heartbeats = tmp_path / ".sdd" / "runtime" / "heartbeats"
    heartbeats.mkdir(parents=True, exist_ok=True)
    (heartbeats / "A-1.json").write_text(
        json.dumps(
            {
                "timestamp": time.time(),
                "files_changed": 1,
                "status": "working",
                "current_file": "src/app.py",
                "phase": "implementing",
                "progress_pct": 50,
                "message": "still working",
            }
        ),
        encoding="utf-8",
    )
    snapshot = {
        "timestamp": 10.0,
        "files_changed": 1,
        "tests_passing": 2,
        "errors": 0,
        "last_file": "src/app.py",
    }
    orch = SimpleNamespace(
        _workdir=tmp_path,
        _agents={"A-1": session},
        _config=SimpleNamespace(server_url="http://server", heartbeat_timeout_s=120),
        _client=MagicMock(),
        _signal_mgr=MagicMock(),
        _spawner=MagicMock(),
        _last_snapshot_ts={},
        _last_snapshot={
            "T-1": ProgressSnapshot(timestamp=9.0, files_changed=1, tests_passing=2, errors=0, last_file="src/app.py")
        },
        _stall_counts={"T-1": 10},
        _latest_tasks_by_id={"T-1": make_task(id="T-1")},
    )
    orch._client.get.return_value.json.return_value = [snapshot]
    orch._client.get.return_value.raise_for_status.return_value = None

    check_stalled_tasks(orch)

    orch._signal_mgr.write_wakeup.assert_not_called()
    orch._signal_mgr.write_shutdown.assert_not_called()
    orch._spawner.kill.assert_not_called()
    assert orch._stall_counts["T-1"] == 0
