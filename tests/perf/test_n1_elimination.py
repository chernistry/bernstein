"""Perf benchmark for issue #1728: N+1 elimination via dedicated store methods.

Seeds a sizeable task store directly (skipping route-layer create overhead
to keep the benchmark fast) and asserts each affected route now touches
``store.list_tasks`` no more than once per request. A counter wrapper is
installed on the store before each request and inspected afterwards.

Findings under test:

* ``task_crud.self_create_subtask`` previously did
  ``sum(1 for t in store.list_tasks() if t.parent_task_id == pid)``.
  After the fix it calls ``store.count_subtasks`` (zero ``list_tasks`` calls).
* ``observability_agents`` previously rebuilt ``{t.id: t for t in
  store.list_tasks()}``. The shared ``_tasks_by_id`` helper now materialises
  it once and caches it on ``request.state``.
* ``get_costs_top_tasks`` previously walked ``store.list_tasks()`` for titles.
  The fix uses ``store.get_task(task_id)`` per cost row instead.
* ``export_tasks`` previously sliced in Python. With ``limit`` / ``offset``
  threaded into ``store.list_tasks`` the slice happens inside the store and
  the route still touches ``list_tasks`` exactly once per request.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from bernstein.core.server import create_app
from bernstein.core.tasks.models import Task, TaskStatus

# Issue #1728 calls for 10k tasks. The store is purely in-memory and the
# benchmark only exercises the read paths, so 10k is fast enough to keep in
# the default suite.
_SEED_N = 10_000


def _install_seed_tasks(store: Any, n: int) -> str:
    """Bulk-insert *n* sibling tasks under a single parent without going through
    the create route. We bypass the route layer because the goal here is to
    measure the route reads on a populated store, not the create path.

    Returns the parent task id so the test can call ``self-create`` against it.
    """
    parent = Task(
        id="parent-0",
        title="parent",
        description="parent",
        role="backend",
        status=TaskStatus.WAITING_FOR_SUBTASKS,
    )
    store._tasks[parent.id] = parent
    store._index_add(parent)
    store._parent_index_add(parent)

    for i in range(n):
        task = Task(
            id=f"t-{i:05d}",
            title=f"task-{i}",
            description="seed",
            role="backend",
            status=TaskStatus.DONE,
            parent_task_id=parent.id,
        )
        store._tasks[task.id] = task
        store._index_add(task)
        store._parent_index_add(task)

    return parent.id


class _CountingListTasks:
    """Wrapper around ``store.list_tasks`` that records how many times it was
    invoked during a single request. The wrapper proxies through to the real
    method so route logic still sees consistent results.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._original = store.list_tasks
        self.calls: int = 0

    def __enter__(self) -> _CountingListTasks:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return self._original(*args, **kwargs)

        self._store.list_tasks = wrapper  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc: object) -> None:
        self._store.list_tasks = self._original  # type: ignore[method-assign]


@pytest.fixture()
def jsonl_path(tmp_path: Path) -> Path:
    return tmp_path / ".sdd" / "runtime" / "tasks.jsonl"


@pytest.fixture()
def app(jsonl_path: Path) -> FastAPI:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    return create_app(jsonl_path=jsonl_path)


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio()
async def test_self_create_does_not_walk_list_tasks(client: AsyncClient, app: FastAPI) -> None:
    """Finding 1: self-create now uses ``count_subtasks`` (O(1))."""
    store = app.state.store
    parent_id = _install_seed_tasks(store, _SEED_N)

    with _CountingListTasks(store) as counter:
        resp = await client.post(
            "/tasks/self-create",
            json={
                "parent_task_id": parent_id,
                "title": "self-created child",
                "description": "perf benchmark child",
                "role": "backend",
                "priority": 2,
                "scope": "small",
                "complexity": "low",
            },
        )

    assert resp.status_code in (200, 201), resp.text
    assert counter.calls == 0, (
        f"Self-create should not materialise list_tasks(); saw {counter.calls} calls. "
        "The count_subtasks index is meant to keep this O(1)."
    )


@pytest.mark.anyio()
async def test_observability_agents_at_most_one_list_tasks(client: AsyncClient, app: FastAPI) -> None:
    """Finding 2: ``/observability/agents`` touches ``list_tasks`` at most once."""
    store = app.state.store
    _install_seed_tasks(store, _SEED_N)

    # Seed an agents.json snapshot so the route has something to iterate.
    sdd_dir = Path(str(app.state.sdd_dir))
    runtime_dir = sdd_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "agent-1", "role": "backend", "status": "working", "task_ids": ["t-00001"]},
                    {"id": "agent-2", "role": "backend", "status": "idle", "task_ids": ["t-00002"]},
                ]
            }
        )
    )

    with _CountingListTasks(store) as counter:
        resp = await client.get("/observability/agents")

    assert resp.status_code == 200, resp.text
    assert counter.calls <= 1, (
        f"/observability/agents should materialise list_tasks at most once per request; saw {counter.calls}."
    )


@pytest.mark.anyio()
async def test_costs_top_tasks_does_not_walk_list_tasks(client: AsyncClient, app: FastAPI) -> None:
    """Finding 3a: ``/costs/top-tasks`` now reads titles via ``get_task`` per id."""
    store = app.state.store
    _install_seed_tasks(store, _SEED_N)

    with _CountingListTasks(store) as counter:
        resp = await client.get("/costs/top-tasks?limit=5&hours=24")

    assert resp.status_code == 200, resp.text
    assert counter.calls == 0, f"/costs/top-tasks should not walk list_tasks(); saw {counter.calls} calls."


@pytest.mark.anyio()
async def test_export_tasks_calls_list_tasks_once(client: AsyncClient, app: FastAPI) -> None:
    """Finding 3b: ``/export/tasks`` still uses ``list_tasks`` once, but with
    ``limit`` / ``offset`` pushed down so callers can scope the export."""
    store = app.state.store
    _install_seed_tasks(store, _SEED_N)

    with _CountingListTasks(store) as counter:
        resp = await client.get("/export/tasks?format=json&limit=10")

    assert resp.status_code == 200, resp.text
    assert counter.calls == 1, (
        f"/export/tasks should materialise list_tasks exactly once per request; saw {counter.calls}."
    )

    payload = json.loads(resp.text)
    assert len(payload) == 10, "limit=10 should be honoured by the store, not by post-slicing"
