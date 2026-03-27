"""Shared pytest fixtures for the bernstein test suite."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bernstein.adapters.base import CLIAdapter, SpawnResult
from bernstein.core.models import (
    Complexity,
    Scope,
    Task,
    TaskStatus,
    TaskType,
)


@pytest.fixture
def make_task():
    """Factory fixture for Task objects with sensible defaults.

    Supports all common Task fields; tests override only what they care about.
    """

    def _factory(
        *,
        id: str = "T-001",
        role: str = "backend",
        title: str = "Implement feature",
        description: str = "Write the code.",
        scope: Scope = Scope.MEDIUM,
        complexity: Complexity = Complexity.MEDIUM,
        status: TaskStatus = TaskStatus.OPEN,
        task_type: TaskType = TaskType.STANDARD,
        priority: int = 2,
        owned_files: list[str] | None = None,
    ) -> Task:
        return Task(
            id=id,
            title=title,
            description=description,
            role=role,
            scope=scope,
            complexity=complexity,
            status=status,
            task_type=task_type,
            priority=priority,
            owned_files=owned_files or [],
        )

    return _factory


@pytest.fixture
def mock_adapter_factory():
    """Factory fixture for CLIAdapter mocks with configurable PID."""

    def _factory(pid: int = 42) -> CLIAdapter:
        adapter = MagicMock(spec=CLIAdapter)
        adapter.spawn.return_value = SpawnResult(pid=pid, log_path=Path("/tmp/test.log"))
        adapter.is_alive.return_value = True
        adapter.kill.return_value = None
        adapter.name.return_value = "MockCLI"
        return adapter

    return _factory


@pytest.fixture
def sdd_dir(tmp_path: Path) -> Path:
    """Temporary .sdd directory with standard subdirectories pre-created."""
    sdd = tmp_path / ".sdd"
    (sdd / "backlog" / "open").mkdir(parents=True)
    (sdd / "backlog" / "done").mkdir(parents=True)
    (sdd / "runtime").mkdir(parents=True)
    (sdd / "metrics").mkdir(parents=True)
    (sdd / "upgrades").mkdir(parents=True)
    return sdd
