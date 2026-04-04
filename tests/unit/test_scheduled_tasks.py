"""Tests for scheduled health-check task injection."""

from __future__ import annotations

import json
from pathlib import Path

from bernstein.core.models import Complexity, Scope, Task, TaskStatus, TaskType
from bernstein.core.spawner import _health_check_interval, _inject_scheduled_tasks


def _make_task(
    *,
    id: str = "T-001",
    title: str = "Test task",
    role: str = "backend",
    estimated_minutes: int = 30,
    complexity: Complexity = Complexity.MEDIUM,
) -> Task:
    return Task(
        id=id,
        title=title,
        description="A test task",
        role=role,
        estimated_minutes=estimated_minutes,
        complexity=complexity,
        scope=Scope.MEDIUM,
        status=TaskStatus.OPEN,
        task_type=TaskType.STANDARD,
        priority=2,
    )


class TestHealthCheckInterval:
    def test_empty_tasks_returns_five(self) -> None:
        assert _health_check_interval([]) == 5

    def test_short_task_returns_three(self) -> None:
        tasks = [_make_task(estimated_minutes=10)]
        assert _health_check_interval(tasks) == 3

    def test_medium_task_returns_five(self) -> None:
        tasks = [_make_task(estimated_minutes=30)]
        assert _health_check_interval(tasks) == 5

    def test_long_task_returns_ten(self) -> None:
        tasks = [_make_task(estimated_minutes=90)]
        assert _health_check_interval(tasks) == 10

    def test_boundary_exactly_15_returns_five(self) -> None:
        # 15 min is not < 15, so returns 5 (not 3)
        tasks = [_make_task(estimated_minutes=15)]
        assert _health_check_interval(tasks) == 5

    def test_boundary_exactly_60_returns_five(self) -> None:
        # 60 min is not > 60, so returns 5 (not 10)
        tasks = [_make_task(estimated_minutes=60)]
        assert _health_check_interval(tasks) == 5

    def test_batch_uses_max_estimated(self) -> None:
        # If any task in the batch is long, use the longer interval
        tasks = [
            _make_task(estimated_minutes=10),
            _make_task(id="T-002", estimated_minutes=90),
        ]
        assert _health_check_interval(tasks) == 10


class TestInjectScheduledTasks:
    def test_creates_file(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="backend-abc12345")
        assert (workdir / ".claude" / "scheduled_tasks.json").exists()

    def test_creates_claude_directory(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="qa-xyz")
        assert (workdir / ".claude").is_dir()

    def test_valid_json(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="backend-deadbeef")
        content = (workdir / ".claude" / "scheduled_tasks.json").read_text()
        parsed = json.loads(content)
        assert "tasks" in parsed
        assert isinstance(parsed["tasks"], list)
        assert len(parsed["tasks"]) == 1

    def test_task_id_uses_session_prefix(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="backend-deadbeef-extra")
        data = json.loads((workdir / ".claude" / "scheduled_tasks.json").read_text())
        task_id = data["tasks"][0]["id"]
        assert task_id == "hc-backend-"

    def test_cron_expression_uses_interval(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="s-1", health_interval_minutes=3)
        data = json.loads((workdir / ".claude" / "scheduled_tasks.json").read_text())
        assert data["tasks"][0]["cron"] == "*/3 * * * *"

    def test_default_interval_five_minutes(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="s-2")
        data = json.loads((workdir / ".claude" / "scheduled_tasks.json").read_text())
        assert data["tasks"][0]["cron"] == "*/5 * * * *"

    def test_recurring_is_true(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="s-3")
        data = json.loads((workdir / ".claude" / "scheduled_tasks.json").read_text())
        assert data["tasks"][0]["recurring"] is True

    def test_prompt_mentions_stuck_and_token_budget(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="s-4")
        data = json.loads((workdir / ".claude" / "scheduled_tasks.json").read_text())
        prompt = data["tasks"][0]["prompt"]
        assert "stuck" in prompt
        assert "token" in prompt.lower() or "budget" in prompt.lower()

    def test_created_at_is_integer_milliseconds(self, tmp_path: Path) -> None:
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        _inject_scheduled_tasks(workdir, session_id="s-5")
        data = json.loads((workdir / ".claude" / "scheduled_tasks.json").read_text())
        created_at = data["tasks"][0]["createdAt"]
        assert isinstance(created_at, int)
        # Should be recent (2020+ epoch ms)
        assert created_at > 1_577_836_800_000

    def test_silently_skips_when_workdir_unwritable(self, tmp_path: Path) -> None:
        # Non-existent workdir: mkdir=True should still create it
        workdir = tmp_path / "new_workdir"
        # Should not raise even if workdir doesn't exist yet
        _inject_scheduled_tasks(workdir, session_id="s-6")
        assert (workdir / ".claude" / "scheduled_tasks.json").exists()
