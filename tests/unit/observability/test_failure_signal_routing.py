"""Pin the wiring of genuine failure signals into the error-capture helper.

These tests assert that the three real failure surfaces route to
:mod:`bernstein.core.observability.error_capture`, and -- just as
importantly -- that expected control-flow outcomes do NOT. They patch the
capture helper so no network is touched; the helper's own transport
behaviour is covered in ``test_error_capture.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from bernstein.core.agents import agent_lifecycle
from bernstein.core.autofix import daemon as autofix_daemon
from bernstein.core.observability import error_capture
from bernstein.core.tasks import task_lifecycle
from bernstein.core.tasks.dead_letter_queue import DLQEntry
from bernstein.core.tasks.models import AbortReason, AgentSession


class _CaptureRecorder:
    """Records calls to the capture helper's two entry points."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.exceptions: list[tuple[BaseException, str]] = []

    def capture_message(self, message: str, *, category: str, **_kw: Any) -> None:
        self.messages.append((message, category))

    def capture_exception(self, exc: BaseException, *, category: str, **_kw: Any) -> None:
        self.exceptions.append((exc, category))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _CaptureRecorder:
    rec = _CaptureRecorder()
    monkeypatch.setattr(error_capture, "capture_message", rec.capture_message)
    monkeypatch.setattr(error_capture, "capture_exception", rec.capture_exception)
    return rec


# ---------------------------------------------------------------------------
# Dead-letter path
# ---------------------------------------------------------------------------


def test_dead_letter_enqueue_routes_to_capture(recorder: _CaptureRecorder) -> None:
    """A DLQ entry is forwarded to the error sink as a message."""
    entry = DLQEntry(
        id="e1",
        task_id="T-1",
        title="add auth",
        role="backend",
        reason="max_retries_exhausted",
        retry_count=3,
        original_error="boom",
    )

    task_lifecycle._capture_dead_letter(entry, original_error="boom")

    assert len(recorder.messages) == 1
    message, category = recorder.messages[0]
    assert category == "dead_letter"
    assert "max_retries_exhausted" in message


# ---------------------------------------------------------------------------
# Agent crash path
# ---------------------------------------------------------------------------


def test_agent_crash_routes_to_capture(recorder: _CaptureRecorder) -> None:
    """An OOM crash is forwarded to the error sink."""
    session = AgentSession(id="s1", role="backend")

    agent_lifecycle._capture_agent_crash(session, AbortReason.OOM, "killed by SIGKILL")

    assert len(recorder.messages) == 1
    message, category = recorder.messages[0]
    assert category == "agent"
    assert "oom" in message


@pytest.mark.parametrize(
    "reason",
    [
        AbortReason.USER_INTERRUPT,
        AbortReason.SHUTDOWN_SIGNAL,
        AbortReason.SIBLING_ABORTED,
        AbortReason.PARENT_ABORTED,
    ],
)
def test_expected_aborts_do_not_route_to_capture(
    recorder: _CaptureRecorder,
    reason: AbortReason,
) -> None:
    """Deliberate aborts are control-flow, not incidents; nothing is captured."""
    session = AgentSession(id="s1", role="backend")

    agent_lifecycle._capture_agent_crash(session, reason, "deliberate")

    assert recorder.messages == []


# ---------------------------------------------------------------------------
# Autofix daemon fault path
# ---------------------------------------------------------------------------


def test_autofix_fault_routes_to_capture(recorder: _CaptureRecorder) -> None:
    """A daemon fault forwards the exception to the error sink."""
    boom = RuntimeError("dispatch exploded")

    autofix_daemon._capture_autofix_fault(boom, stage="dispatch", repo="o/r", pr_number=7)

    assert len(recorder.exceptions) == 1
    exc, category = recorder.exceptions[0]
    assert exc is boom
    assert category == "autofix"
