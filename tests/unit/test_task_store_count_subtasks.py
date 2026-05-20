"""Tests for issue #1728 finding 1: TaskStore.count_subtasks.

Replaces the O(N) ``sum(1 for t in store.list_tasks() if t.parent_task_id == pid)``
walk in ``task_crud.py:571`` with an O(1) lookup against a ``_by_parent``
index. The index is maintained atomically with the existing ``_by_status``
and ``_by_role_status`` indices inside ``self._lock``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bernstein.core.task_store import TaskStore


def _task_request(
    *,
    title: str = "t",
    description: str = "d",
    role: str = "backend",
    priority: int = 2,
    scope: str = "small",
    complexity: str = "low",
    parent_task_id: str | None = None,
    depends_on: list[str] | None = None,
) -> Any:
    """Build a minimal TaskCreate-shaped request."""
    return SimpleNamespace(
        title=title,
        description=description,
        role=role,
        priority=priority,
        scope=scope,
        complexity=complexity,
        estimated_minutes=30,
        depends_on=depends_on or [],
        owned_files=[],
        cell_id=None,
        task_type="standard",
        upgrade_details=None,
        model=None,
        effort=None,
        batch_eligible=False,
        completion_signals=[],
        slack_context=None,
        parent_task_id=parent_task_id,
        tenant_id=None,
    )


@pytest.mark.anyio
async def test_count_subtasks_returns_zero_for_unknown_parent(tmp_path: Path) -> None:
    """Unknown parent ids return 0 instead of walking ``list_tasks()``."""
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    assert store.count_subtasks("nonexistent") == 0


@pytest.mark.anyio
async def test_count_subtasks_tracks_children_on_create(tmp_path: Path) -> None:
    """Each create with parent_task_id must bump the parent->children count."""
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")

    parent = await store.create(_task_request(title="parent"))
    assert store.count_subtasks(parent.id) == 0

    await store.create(_task_request(title="child-1", parent_task_id=parent.id))
    assert store.count_subtasks(parent.id) == 1

    await store.create(_task_request(title="child-2", parent_task_id=parent.id))
    await store.create(_task_request(title="child-3", parent_task_id=parent.id))
    assert store.count_subtasks(parent.id) == 3

    # Sibling parent gets its own bucket.
    other_parent = await store.create(_task_request(title="other-parent"))
    await store.create(_task_request(title="other-child", parent_task_id=other_parent.id))
    assert store.count_subtasks(other_parent.id) == 1
    assert store.count_subtasks(parent.id) == 3


@pytest.mark.anyio
async def test_count_subtasks_does_not_call_list_tasks(tmp_path: Path) -> None:
    """The O(1) path must not fall back to ``list_tasks()`` materialisation."""
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    parent = await store.create(_task_request(title="parent"))
    for i in range(50):
        await store.create(_task_request(title=f"child-{i}", parent_task_id=parent.id))

    calls = {"n": 0}
    original = store.list_tasks

    def counting_list_tasks(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return original(*args, **kwargs)

    store.list_tasks = counting_list_tasks  # type: ignore[method-assign]
    assert store.count_subtasks(parent.id) == 50
    assert calls["n"] == 0, "count_subtasks must not materialise list_tasks()"


@pytest.mark.anyio
async def test_parent_index_atomic_under_concurrent_creates(tmp_path: Path) -> None:
    """The ``_by_parent`` index updates under the same lock as ``_by_status``.

    Schedule many concurrent ``create`` calls and assert the final count
    matches the number of children. A race in the index would yield a count
    less than ``n`` (lost updates) or a divergence between ``count_subtasks``
    and the authoritative ``_tasks`` walk.
    """
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    parent = await store.create(_task_request(title="parent"))

    n = 64
    await asyncio.gather(*(store.create(_task_request(title=f"c-{i}", parent_task_id=parent.id)) for i in range(n)))

    by_walk = sum(1 for t in store.list_tasks() if t.parent_task_id == parent.id)
    assert store.count_subtasks(parent.id) == n
    assert store.count_subtasks(parent.id) == by_walk


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
