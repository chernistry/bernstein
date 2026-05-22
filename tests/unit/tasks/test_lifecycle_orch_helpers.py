"""Behavioral tests for orchestrator-facing helpers in ``task_lifecycle``.

``evict_degraded_sessions`` is exercised against a duck-typed orchestrator
stub (detector / agents / recovery store / signal manager). The permission
helper is tested for its retry-decision logic and degraded (no-hint) path.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bernstein.core.tasks.models import AgentSession
from bernstein.core.tasks.task_lifecycle import (
    evict_degraded_sessions,
    handle_permission_denied_error,
)


class _Checkpoint:
    def __init__(self) -> None:
        self.task_ids = ["t1", "t2"]
        self.recovery_context = "recover me"
        self.consecutive_rejects = 3
        self.verdict_count = 3


class _Detector:
    def __init__(self, degraded: list[str]) -> None:
        self._degraded = degraded
        self.cleared: list[str] = []
        self.raise_on_checkpoint = False

    def degraded_sessions(self) -> list[str]:
        return list(self._degraded)

    def checkpoint(self, _session: Any) -> _Checkpoint:
        if self.raise_on_checkpoint:
            raise RuntimeError("checkpoint failed")
        return _Checkpoint()

    def clear(self, session_id: str) -> None:
        self.cleared.append(session_id)


class _SignalMgr:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    def write_shutdown(self, session_id: str, *, reason: str = "", task_title: str = "") -> None:
        self.writes.append((session_id, reason, task_title))


def _agent(status: str = "working") -> AgentSession:
    return AgentSession(id="sess-1", role="backend", status=status)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# evict_degraded_sessions
# ---------------------------------------------------------------------------


def test_evict_returns_empty_when_detector_disabled() -> None:
    orch = SimpleNamespace(_context_degradation=None)
    assert evict_degraded_sessions(orch) == []


def test_evict_checkpoints_alive_session_and_signals_shutdown() -> None:
    detector = _Detector(["sess-1"])
    signal_mgr = _SignalMgr()
    recovery: dict[str, str] = {}
    orch = SimpleNamespace(
        _context_degradation=detector,
        _agents={"sess-1": _agent("working")},
        _context_recovery=recovery,
        _signal_mgr=signal_mgr,
    )
    evicted = evict_degraded_sessions(orch)
    assert evicted == ["sess-1"]
    # Recovery context stashed for every task the session owned.
    assert recovery["t1"] == "recover me"
    assert recovery["t2"] == "recover me"
    # SHUTDOWN signal written with the degradation reason.
    assert signal_mgr.writes == [("sess-1", "context_degradation", "t1, t2")]
    # Detector state cleared after eviction.
    assert detector.cleared == ["sess-1"]


def test_evict_skips_dead_session_but_clears_tracking() -> None:
    detector = _Detector(["sess-1"])
    orch = SimpleNamespace(
        _context_degradation=detector,
        _agents={"sess-1": _agent("dead")},
        _context_recovery={},
        _signal_mgr=_SignalMgr(),
    )
    assert evict_degraded_sessions(orch) == []
    assert detector.cleared == ["sess-1"]


def test_evict_skips_missing_session_but_clears_tracking() -> None:
    detector = _Detector(["sess-1"])
    orch = SimpleNamespace(
        _context_degradation=detector,
        _agents={},  # session no longer present
        _context_recovery={},
        _signal_mgr=_SignalMgr(),
    )
    assert evict_degraded_sessions(orch) == []
    assert detector.cleared == ["sess-1"]


def test_evict_clears_tracking_when_checkpoint_raises() -> None:
    detector = _Detector(["sess-1"])
    detector.raise_on_checkpoint = True
    orch = SimpleNamespace(
        _context_degradation=detector,
        _agents={"sess-1": _agent("working")},
        _context_recovery={},
        _signal_mgr=_SignalMgr(),
    )
    # A checkpoint failure must not evict, but must clear tracking state.
    assert evict_degraded_sessions(orch) == []
    assert detector.cleared == ["sess-1"]


# ---------------------------------------------------------------------------
# handle_permission_denied_error
# ---------------------------------------------------------------------------


def test_permission_denied_with_hint_allows_retry_under_limit() -> None:
    result = handle_permission_denied_error("Permission denied for Edit tool", "t1", "backend", 0)
    assert result["permission_denied"] is True
    assert result["hint"] is not None
    assert result["should_retry"] is True
    assert result["max_retries"] == 2


def test_permission_denied_with_hint_blocks_retry_at_limit() -> None:
    result = handle_permission_denied_error("Permission denied for Edit tool", "t1", "backend", 2)
    assert result["should_retry"] is False


def test_permission_denied_without_hint_does_not_retry() -> None:
    # A message with no recognised hint pattern routes to the no-hint branch.
    result = handle_permission_denied_error("totally unrecognised failure text", "t1", "backend", 0)
    assert result["permission_denied"] is True
    assert result["hint"] is None
    assert result["should_retry"] is False
