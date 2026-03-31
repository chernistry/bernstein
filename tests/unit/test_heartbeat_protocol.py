"""Unit tests for the heartbeat monitor."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bernstein.core.agent_signals import AgentSignalManager
from bernstein.core.heartbeat import HeartbeatMonitor
from bernstein.core.models import AgentHeartbeat


def _write_primary_heartbeat(tmp_path: Path, session_id: str, *, age_s: float) -> None:
    manager = AgentSignalManager(tmp_path)
    manager.write_heartbeat(
        session_id,
        AgentHeartbeat(
            timestamp=time.time(),
            files_changed=1,
            status="working",
            current_file="src/app.py",
            phase="implementing",
            progress_pct=45,
            message="writing tests",
        ),
    )
    path = tmp_path / ".sdd" / "runtime" / "heartbeats" / f"{session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["timestamp"] = time.time() - age_s
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_check_fresh_heartbeat(tmp_path: Path) -> None:
    _write_primary_heartbeat(tmp_path, "A-1", age_s=10)

    status = HeartbeatMonitor(tmp_path, timeout_s=120).check("A-1")

    assert status.is_alive is True
    assert status.is_stale is False
    assert status.phase == "implementing"
    assert status.progress_pct == 45


def test_check_stale_heartbeat(tmp_path: Path) -> None:
    _write_primary_heartbeat(tmp_path, "A-2", age_s=180)

    status = HeartbeatMonitor(tmp_path, timeout_s=120).check("A-2")

    assert status.is_alive is False
    assert status.is_stale is True
    assert status.age_seconds >= 120


def test_check_missing_heartbeat(tmp_path: Path) -> None:
    status = HeartbeatMonitor(tmp_path, timeout_s=120).check("missing")

    assert status.last_heartbeat is None
    assert status.is_alive is False
    assert status.is_stale is False


def test_check_malformed_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / ".sdd" / "runtime" / "signals" / "A-3"
    path.mkdir(parents=True)
    (path / "HEARTBEAT").write_text("{not-json", encoding="utf-8")

    status = HeartbeatMonitor(tmp_path, timeout_s=120).check("A-3")

    assert status.last_heartbeat is None
    assert status.is_alive is False


def test_inject_heartbeat_instructions(tmp_path: Path) -> None:
    snippet = HeartbeatMonitor(tmp_path).inject_heartbeat_instructions("A-4")

    assert "A-4" in snippet
    assert ".sdd/runtime/heartbeats/A-4.json" in snippet
    assert "sleep 15" in snippet


def test_check_all_mixed(tmp_path: Path) -> None:
    _write_primary_heartbeat(tmp_path, "fresh", age_s=5)
    _write_primary_heartbeat(tmp_path, "stale", age_s=200)
    fallback_dir = tmp_path / ".sdd" / "runtime" / "signals" / "missing-format"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    (fallback_dir / "HEARTBEAT").write_text(
        json.dumps(
            {
                "timestamp": (datetime.now(UTC) - timedelta(seconds=20)).isoformat(),
                "phase": "testing",
                "progress_pct": 75,
                "current_file": "tests/test_app.py",
                "message": "running tests",
            }
        ),
        encoding="utf-8",
    )

    statuses = HeartbeatMonitor(tmp_path, timeout_s=120).check_all(["fresh", "stale", "missing-format", "none"])
    by_id = {status.session_id: status for status in statuses}

    assert by_id["fresh"].is_alive is True
    assert by_id["stale"].is_stale is True
    assert by_id["missing-format"].phase == "testing"
    assert by_id["none"].last_heartbeat is None
