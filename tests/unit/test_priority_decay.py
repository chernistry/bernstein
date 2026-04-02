"""Tests for priority decay of old unclaimed tasks."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from bernstein.core.models import Task, TaskStatus
from bernstein.core.task_lifecycle import deprioritize_old_unclaimed_tasks
from bernstein.core.task_store import TaskStore


class TestPriorityDecay:
    """Test priority decay for old unclaimed tasks."""

    def test_deprioritize_old_task(self, tmp_path: Path) -> None:
        """Test that old open tasks are deprioritized."""
        orch = MagicMock()
        jsonl_path = tmp_path / "tasks.jsonl"
        store = TaskStore(jsonl_path=jsonl_path)

        old_time = time.time() - (25 * 3600)
        old_task = Task(
            id="old-task",
            title="Old unclaimed task",
            description="This task is old",
            role="backend",
            priority=2,
            status=TaskStatus.OPEN,
            created_at=old_time,
        )
        store._tasks["old-task"] = old_task  # type: ignore[reportPrivateUsage]
        store._index_add(old_task)  # type: ignore[reportPrivateUsage]

        orch._store = store

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=24)

        assert count == 1
        assert store._tasks["old-task"].priority == 3  # type: ignore[reportPrivateUsage]

    def test_no_deprioritize_recent_task(self, tmp_path: Path) -> None:
        """Test that recent tasks are not deprioritized."""
        orch = MagicMock()
        jsonl_path = tmp_path / "tasks.jsonl"
        store = TaskStore(jsonl_path=jsonl_path)

        recent_time = time.time() - 3600
        recent_task = Task(
            id="recent-task",
            title="Recent task",
            description="This task is new",
            role="backend",
            priority=2,
            status=TaskStatus.OPEN,
            created_at=recent_time,
        )
        store._tasks["recent-task"] = recent_task  # type: ignore[reportPrivateUsage]
        store._index_add(recent_task)  # type: ignore[reportPrivateUsage]

        orch._store = store

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=24)

        assert count == 0
        assert store._tasks["recent-task"].priority == 2  # type: ignore[reportPrivateUsage]

    def test_no_deprioritize_non_open_tasks(self, tmp_path: Path) -> None:
        """Test that non-open tasks are not deprioritized."""
        orch = MagicMock()
        jsonl_path = tmp_path / "tasks.jsonl"
        store = TaskStore(jsonl_path=jsonl_path)

        old_time = time.time() - (25 * 3600)
        claimed_task = Task(
            id="claimed-task",
            title="Claimed task",
            description="This task is claimed",
            role="backend",
            priority=2,
            status=TaskStatus.CLAIMED,
            created_at=old_time,
        )
        store._tasks["claimed-task"] = claimed_task  # type: ignore[reportPrivateUsage]
        store._index_add(claimed_task)  # type: ignore[reportPrivateUsage]

        orch._store = store

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=24)

        assert count == 0
        assert store._tasks["claimed-task"].priority == 2  # type: ignore[reportPrivateUsage]

    def test_priority_floor(self, tmp_path: Path) -> None:
        """Test that priority doesn't go below minimum."""
        orch = MagicMock()
        jsonl_path = tmp_path / "tasks.jsonl"
        store = TaskStore(jsonl_path=jsonl_path)

        old_time = time.time() - (25 * 3600)
        old_task = Task(
            id="old-task",
            title="Old task at min priority",
            description="This task is at min priority",
            role="backend",
            priority=3,
            status=TaskStatus.OPEN,
            created_at=old_time,
        )
        store._tasks["old-task"] = old_task  # type: ignore[reportPrivateUsage]
        store._index_add(old_task)  # type: ignore[reportPrivateUsage]

        orch._store = store

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=24, min_priority=3)

        assert count == 0
        assert store._tasks["old-task"].priority == 3  # type: ignore[reportPrivateUsage]

    def test_multiple_old_tasks(self, tmp_path: Path) -> None:
        """Test deprioritizing multiple old tasks."""
        orch = MagicMock()
        jsonl_path = tmp_path / "tasks.jsonl"
        store = TaskStore(jsonl_path=jsonl_path)

        old_time = time.time() - (25 * 3600)
        for i in range(3):
            task = Task(
                id=f"old-task-{i}",
                title=f"Old task {i}",
                description="Old task",
                role="backend",
                priority=2,
                status=TaskStatus.OPEN,
                created_at=old_time,
            )
            store._tasks[f"old-task-{i}"] = task  # type: ignore[reportPrivateUsage]
            store._index_add(task)  # type: ignore[reportPrivateUsage]

        orch._store = store

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=24)

        assert count == 3
        for i in range(3):
            assert store._tasks[f"old-task-{i}"].priority == 3  # type: ignore[reportPrivateUsage]

    def test_custom_threshold(self, tmp_path: Path) -> None:
        """Test custom threshold hours."""
        orch = MagicMock()
        jsonl_path = tmp_path / "tasks.jsonl"
        store = TaskStore(jsonl_path=jsonl_path)

        old_time = time.time() - (5 * 3600)
        task = Task(
            id="task-5h",
            title="5 hour old task",
            description="Old task",
            role="backend",
            priority=2,
            status=TaskStatus.OPEN,
            created_at=old_time,
        )
        store._tasks["task-5h"] = task  # type: ignore[reportPrivateUsage]
        store._index_add(task)  # type: ignore[reportPrivateUsage]

        orch._store = store

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=24)
        assert count == 0

        count = deprioritize_old_unclaimed_tasks(orch, threshold_hours=4)
        assert count == 1
        assert store._tasks["task-5h"].priority == 3  # type: ignore[reportPrivateUsage]
