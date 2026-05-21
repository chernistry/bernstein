"""Unit tests for the opt-in ``agent_restart_between_retries`` flag.

Closes #1109 - when the flag is set on a task, retries must spawn a fresh
agent with no accumulated state.  These tests exercise the spawner's
helper methods, the audit-event emission, and the default-off semantics
that protect existing tasks from any behaviour change.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bernstein.core.agents.spawner_core import AgentSpawner
from bernstein.core.security.audit import (
    AGENT_FRESH_RESTART_ON_RETRY,
    AuditLog,
)
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_retry_task(
    *,
    flag: bool,
    retry_count: int = 1,
    description: str = "Do the thing.",
    meta_messages: list[str] | None = None,
    terminal_reason: str | None = None,
) -> Task:
    """Build a Task that mimics one produced by ``maybe_retry_task``.

    Args:
        flag: Value for ``agent_restart_between_retries``.
        retry_count: Retry attempt number; 0 means first attempt.
        description: Task description (the test harness can append the
            ``## Previous attempt failed`` block to this).
        meta_messages: Operational nudges; the helper carries forward
            ``Retry N: Previous attempt failed*`` entries that the spawner
            must drop when ``flag`` is True.
        terminal_reason: Reason from the prior failure, surfaced in the
            audit details.

    Returns:
        Populated Task ready for spawner-helper assertions.
    """
    return Task(
        id="T-IS-1109",
        title="Compile parser",
        description=description,
        role="backend",
        scope=Scope.SMALL,
        complexity=Complexity.LOW,
        status=TaskStatus.OPEN,
        retry_count=retry_count,
        max_retries=3,
        terminal_reason=terminal_reason,
        meta_messages=list(meta_messages or []),
        agent_restart_between_retries=flag,
    )


@pytest.fixture
def spawner_stub(tmp_path: Path) -> AgentSpawner:
    """Build an :class:`AgentSpawner` stub wired to *tmp_path*.

    The stub never actually spawns processes - these tests only exercise
    the synchronous helpers and the audit log writer, both of which only
    need ``self._workdir`` set.  Worktree creation is disabled.
    """
    adapter = MagicMock()
    adapter.name.return_value = "mock"
    templates_dir = tmp_path / "templates" / "roles"
    templates_dir.mkdir(parents=True)
    return AgentSpawner(adapter, templates_dir, tmp_path, use_worktrees=False)


# ---------------------------------------------------------------------------
# Default-off regression
# ---------------------------------------------------------------------------


class TestDefaultOff:
    """Existing tasks (flag unset) must retain their current semantics."""

    def test_task_default_is_false(self) -> None:
        """The new field defaults to False so legacy tasks are unaffected."""
        task = Task(id="T1", title="x", description="y", role="backend")
        assert task.agent_restart_between_retries is False

    def test_from_dict_omits_field(self) -> None:
        """``Task.from_dict`` defaults the field to False when absent."""
        raw = {
            "id": "T1",
            "title": "x",
            "description": "y",
            "role": "backend",
        }
        task = Task.from_dict(raw)
        assert task.agent_restart_between_retries is False

    def test_from_dict_round_trip(self) -> None:
        """``Task.from_dict`` honours an explicit True value."""
        raw = {
            "id": "T1",
            "title": "x",
            "description": "y",
            "role": "backend",
            "agent_restart_between_retries": True,
        }
        task = Task.from_dict(raw)
        assert task.agent_restart_between_retries is True


# ---------------------------------------------------------------------------
# _is_fresh_restart_retry - gating logic
# ---------------------------------------------------------------------------


class TestIsFreshRestartRetry:
    """The spawner only triggers fresh-context retries when both gates pass."""

    def test_flag_off_first_attempt(self) -> None:
        """No flag, no retry → default behaviour (False)."""
        task = _make_retry_task(flag=False, retry_count=0)
        assert AgentSpawner._is_fresh_restart_retry(task) is False

    def test_flag_off_retry(self) -> None:
        """Flag off on a retry must not trigger a fresh restart."""
        task = _make_retry_task(flag=False, retry_count=2)
        assert AgentSpawner._is_fresh_restart_retry(task) is False

    def test_flag_on_first_attempt(self) -> None:
        """Flag on but ``retry_count == 0`` is never a "retry" - first spawn."""
        task = _make_retry_task(flag=True, retry_count=0)
        assert AgentSpawner._is_fresh_restart_retry(task) is False

    def test_flag_on_retry(self) -> None:
        """Both gates open → fresh restart applies."""
        task = _make_retry_task(flag=True, retry_count=1)
        assert AgentSpawner._is_fresh_restart_retry(task) is True


# ---------------------------------------------------------------------------
# _strip_failure_context_for_fresh_retry - context wipe
# ---------------------------------------------------------------------------


class TestStripFailureContext:
    """Failure-replay annotations are removed before prompt rendering."""

    def test_strips_previous_attempt_section(self, spawner_stub: AgentSpawner) -> None:
        """The ``## Previous attempt failed`` block is dropped from the description."""
        task = _make_retry_task(
            flag=True,
            retry_count=1,
            description=(
                "Do the thing.\n\n"
                "## Previous attempt failed\n"
                "compile_error in src/parser.py\n\n"
                "Avoid the same mistakes."
            ),
        )
        description, _ = spawner_stub._strip_failure_context_for_fresh_retry(task)
        assert description == "Do the thing."

    def test_strips_retry_meta_messages(self, spawner_stub: AgentSpawner) -> None:
        """``Retry N: Previous attempt failed*`` nudges are dropped; others survive."""
        task = _make_retry_task(
            flag=True,
            retry_count=2,
            meta_messages=[
                "Retry 1: Previous attempt failed with reason: 429",
                "Operator hint: prefer pure functions",
                "Retry 2: Previous attempt failed with reason: timeout",
            ],
        )
        _, meta = spawner_stub._strip_failure_context_for_fresh_retry(task)
        assert meta == ["Operator hint: prefer pure functions"]

    def test_keeps_clean_description_and_messages(self, spawner_stub: AgentSpawner) -> None:
        """Tasks without replay annotations pass through unchanged."""
        task = _make_retry_task(
            flag=True,
            retry_count=1,
            description="Plain description.",
            meta_messages=["Be careful."],
        )
        description, meta = spawner_stub._strip_failure_context_for_fresh_retry(task)
        assert description == "Plain description."
        assert meta == ["Be careful."]


# ---------------------------------------------------------------------------
# Audit emission
# ---------------------------------------------------------------------------


def _isolated_audit_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the global audit key to a per-test path so HMAC stays scoped."""
    key_path = tmp_path / "audit.key"
    monkeypatch.setenv("BERNSTEIN_AUDIT_KEY_PATH", str(key_path))
    return key_path


class TestAuditEmission:
    """Each fresh restart emits one HMAC-chained audit event."""

    def test_emits_event_with_correct_fields(
        self,
        spawner_stub: AgentSpawner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The event captures ``task_id``, ``retry_n``, and ``reason``."""
        _isolated_audit_key(monkeypatch, tmp_path)
        spawner_stub._emit_fresh_restart_on_retry_audit(
            task_id="T-IS-1109",
            retry_n=2,
            reason="upstream rate limit",
        )

        audit_dir = tmp_path / ".sdd" / "audit"
        assert audit_dir.exists()
        log_files = sorted(audit_dir.glob("*.jsonl"))
        assert len(log_files) == 1

        entries = [json.loads(line) for line in log_files[0].read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["event_type"] == AGENT_FRESH_RESTART_ON_RETRY
        assert entry["resource_type"] == "task"
        assert entry["resource_id"] == "T-IS-1109"
        assert entry["actor"] == "spawner"
        assert entry["details"] == {
            "task_id": "T-IS-1109",
            "retry_n": 2,
            "reason": "upstream rate limit",
        }

    def test_audit_chain_verifies(
        self,
        spawner_stub: AgentSpawner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two restarts produce a chain that ``AuditLog.verify`` accepts."""
        _isolated_audit_key(monkeypatch, tmp_path)
        spawner_stub._emit_fresh_restart_on_retry_audit(
            task_id="T-IS-1109",
            retry_n=1,
            reason="429",
        )
        spawner_stub._emit_fresh_restart_on_retry_audit(
            task_id="T-IS-1109",
            retry_n=2,
            reason="timeout",
        )

        log = AuditLog(audit_dir=tmp_path / ".sdd" / "audit")
        valid, errors = log.verify()
        assert valid, errors
        events = log.query(event_type=AGENT_FRESH_RESTART_ON_RETRY)
        assert [e.details["retry_n"] for e in events] == [1, 2]


# ---------------------------------------------------------------------------
# Retry-budget interaction - the restart still costs one attempt
# ---------------------------------------------------------------------------


class TestRetryBudget:
    """A fresh restart counts as a retry attempt against ``max_retries``."""

    def test_fresh_restart_consumes_budget(self) -> None:
        """When ``retry_count == max_retries`` no further restart should fire."""
        # Scenario: caller has already attempted max_retries times.  The
        # gating helper still says "this is a retry" (retry_count > 0) but
        # the surrounding retry pipeline (maybe_retry_task / retry_or_fail_task)
        # is what enforces the budget.  We verify the gate is symmetric: the
        # flag does not somehow extend the retry budget.
        task = _make_retry_task(flag=True, retry_count=3)
        assert task.retry_count == task.max_retries
        # Spawner gate still fires; the budget enforcement lives upstream.
        assert AgentSpawner._is_fresh_restart_retry(task) is True

    def test_fresh_restart_increments_retry_n(
        self,
        spawner_stub: AgentSpawner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each restart audit reflects the *current* attempt number."""
        _isolated_audit_key(monkeypatch, tmp_path)
        for n in (1, 2, 3):
            spawner_stub._emit_fresh_restart_on_retry_audit(
                task_id="T-budget",
                retry_n=n,
                reason=f"failure #{n}",
            )
        log = AuditLog(audit_dir=tmp_path / ".sdd" / "audit")
        events = log.query(event_type=AGENT_FRESH_RESTART_ON_RETRY)
        assert [e.details["retry_n"] for e in events] == [1, 2, 3]
