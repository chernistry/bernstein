"""Tests for issue #1728 finding 3: ``list_tasks(limit, offset)``.

Costs and export routes previously pulled the full table just to slice
it in Python. ``TaskStore.list_tasks`` now accepts ``limit`` / ``offset``
kwargs so the slice happens inside the store.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bernstein.core.task_store import TaskStore


def _task_request(*, title: str, priority: int = 2) -> Any:
    """Build a minimal TaskCreate-shaped request."""
    return SimpleNamespace(
        title=title,
        description="d",
        role="backend",
        priority=priority,
        scope="small",
        complexity="low",
        estimated_minutes=30,
        depends_on=[],
        owned_files=[],
        cell_id=None,
        task_type="standard",
        upgrade_details=None,
        model=None,
        effort=None,
        batch_eligible=False,
        completion_signals=[],
        slack_context=None,
        parent_task_id=None,
        tenant_id=None,
    )


@pytest.mark.anyio
async def test_list_tasks_limit_caps_result(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    for i in range(10):
        await store.create(_task_request(title=f"t-{i}"))

    sliced = store.list_tasks(limit=3)
    assert len(sliced) == 3


@pytest.mark.anyio
async def test_list_tasks_offset_skips_prefix(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    for i in range(10):
        await store.create(_task_request(title=f"t-{i}"))

    full = store.list_tasks()
    sliced = store.list_tasks(offset=4)
    assert [t.id for t in sliced] == [t.id for t in full[4:]]


@pytest.mark.anyio
async def test_list_tasks_limit_and_offset_combine(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    for i in range(10):
        await store.create(_task_request(title=f"t-{i}"))

    full = store.list_tasks()
    page = store.list_tasks(limit=3, offset=4)
    assert [t.id for t in page] == [t.id for t in full[4:7]]


@pytest.mark.anyio
async def test_list_tasks_no_pagination_unchanged(tmp_path: Path) -> None:
    """When neither kwarg is set, the old return shape is preserved."""
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    for i in range(5):
        await store.create(_task_request(title=f"t-{i}"))

    assert len(store.list_tasks()) == 5


@pytest.mark.anyio
async def test_list_tasks_offset_past_end_returns_empty(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    for i in range(3):
        await store.create(_task_request(title=f"t-{i}"))

    assert store.list_tasks(offset=100) == []
    assert store.list_tasks(limit=10, offset=100) == []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
