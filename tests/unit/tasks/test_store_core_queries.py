"""Behavioral tests for TaskStore query/recovery surfaces and cycle detection.

Targets gaps in ``core/tasks/task_store_core.py``: the static cycle detector,
``list_tasks`` filtering and pagination, ``count_by_status``,
``count_subtasks``, ``get_task``, ``prioritize``, ``cancel``,
``recover_stale_claimed_tasks``, and a JSONL persistence round-trip via
``replay_jsonl``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bernstein.core.tasks.models import Task, TaskStatus
from bernstein.core.tasks.task_store_core import TaskStore


def _req(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "title": "T",
        "description": "D",
        "role": "backend",
        "priority": 2,
        "scope": "medium",
        "complexity": "medium",
        "estimated_minutes": 30,
        "depends_on": [],
        "owned_files": [],
        "cell_id": None,
        "task_type": "standard",
        "upgrade_details": None,
        "model": None,
        "effort": None,
        "batch_eligible": False,
        "completion_signals": [],
        "slack_context": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "runtime" / "tasks.jsonl", archive_path=tmp_path / "archive" / "tasks.jsonl")


def _bare_task(task_id: str, deps: list[str] | None = None) -> Task:
    return Task(id=task_id, title="T", description="D", role="backend", depends_on=deps or [])


# ---------------------------------------------------------------------------
# _detect_cycle (static, pure)
# ---------------------------------------------------------------------------


def test_detect_cycle_returns_none_for_acyclic_graph() -> None:
    tasks = {"a": _bare_task("a"), "b": _bare_task("b", ["a"])}
    new = _bare_task("c", ["b"])
    assert TaskStore._detect_cycle(tasks, new) is None


def test_detect_cycle_flags_self_dependency() -> None:
    new = _bare_task("d", ["d"])
    cycle = TaskStore._detect_cycle({}, new)
    assert cycle is not None
    assert cycle[0] == cycle[-1] == "d"


def test_detect_cycle_flags_indirect_cycle() -> None:
    # a -> x already exists; inserting x -> a closes the loop.
    tasks = {"a": _bare_task("a", ["x"])}
    new = _bare_task("x", ["a"])
    cycle = TaskStore._detect_cycle(tasks, new)
    assert cycle is not None
    assert cycle[0] == cycle[-1]  # cycle path is closed


# ---------------------------------------------------------------------------
# list_tasks filtering and pagination
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tasks_filters_by_cell(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.create(_req(cell_id="cellA"))
    await store.create(_req(cell_id="cellB"))
    assert len(store.list_tasks(cell_id="cellA")) == 1


@pytest.mark.anyio
async def test_list_tasks_filters_by_tenant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.create(_req(tenant_id="tenant-x"))
    await store.create(_req(tenant_id="tenant-y"))
    matches = store.list_tasks(tenant_id="tenant-x")
    assert len(matches) == 1
    assert matches[0].tenant_id == "tenant-x"


@pytest.mark.anyio
async def test_list_tasks_invalid_status_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.create(_req())
    assert store.list_tasks(status="not-a-status") == []


@pytest.mark.anyio
async def test_list_tasks_status_open_excludes_unsatisfied_deps(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dep = await store.create(_req(title="dep"))
    # A task whose dependency is not yet DONE is not "open" for claiming.
    await store.create(_req(title="downstream", depends_on=[dep.id]))
    open_now = store.list_tasks(status="open")
    titles = {t.title for t in open_now}
    assert "dep" in titles
    assert "downstream" not in titles


@pytest.mark.anyio
async def test_list_tasks_pagination_limit_and_offset(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(5):
        await store.create(_req(title=f"task-{i}"))
    assert len(store.list_tasks(limit=2)) == 2
    assert len(store.list_tasks(offset=3)) == 2
    assert len(store.list_tasks(offset=1, limit=2)) == 2


# ---------------------------------------------------------------------------
# count_by_status / count_subtasks / get_task
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_count_by_status_tracks_totals(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.create(_req())
    t2 = await store.create(_req())
    await store.cancel(t2.id, "drop")
    counts = store.count_by_status()
    assert counts["open"] == 1
    assert counts["cancelled"] == 1
    assert counts["total"] == 2


@pytest.mark.anyio
async def test_count_subtasks_counts_direct_children(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = await store.create(_req(title="parent"))
    await store.create(_req(title="c1", parent_task_id=parent.id))
    await store.create(_req(title="c2", parent_task_id=parent.id))
    assert store.count_subtasks(parent.id) == 2


@pytest.mark.anyio
async def test_count_subtasks_zero_for_childless_parent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lone = await store.create(_req())
    assert store.count_subtasks(lone.id) == 0


@pytest.mark.anyio
async def test_get_task_returns_none_for_unknown_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_task("nonexistent") is None


# ---------------------------------------------------------------------------
# prioritize / cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_prioritize_sets_priority_zero_and_bumps_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req(priority=2))
    original_version = task.version
    updated = await store.prioritize(task.id)
    assert updated.priority == 0
    assert updated.version == original_version + 1


@pytest.mark.anyio
async def test_prioritize_unknown_task_raises_keyerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        await store.prioritize("ghost")


@pytest.mark.anyio
async def test_cancel_moves_task_to_cancelled(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    cancelled = await store.cancel(task.id, "no longer needed")
    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.result_summary is not None


# ---------------------------------------------------------------------------
# recover_stale_claimed_tasks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recover_stale_resets_claimed_to_open(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    claimed = await store.claim_next("backend")
    assert claimed is not None
    assert claimed.status is TaskStatus.CLAIMED

    reset = store.recover_stale_claimed_tasks()
    assert reset == 1
    recovered = store.get_task(task.id)
    assert recovered is not None
    assert recovered.status is TaskStatus.OPEN
    assert recovered.claimed_by_session is None
    assert recovered.claimed_at is None


@pytest.mark.anyio
async def test_recover_stale_noop_when_nothing_claimed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await store.create(_req())
    assert store.recover_stale_claimed_tasks() == 0


# ---------------------------------------------------------------------------
# JSONL persistence round-trip
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replay_jsonl_reconstructs_tasks_from_log(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t1 = await store.create(_req(title="alpha", role="qa"))
    t2 = await store.create(_req(title="beta"))
    await store.cancel(t2.id, "drop")
    await store.flush_buffer()

    # A fresh store replaying the same JSONL log must rebuild identical state.
    fresh = _store(tmp_path)
    fresh.replay_jsonl()
    assert len(fresh.list_tasks()) == 2
    replayed_t1 = fresh.get_task(t1.id)
    assert replayed_t1 is not None
    assert replayed_t1.title == "alpha"
    assert replayed_t1.role == "qa"
    replayed_t2 = fresh.get_task(t2.id)
    assert replayed_t2 is not None
    assert replayed_t2.status is TaskStatus.CANCELLED


@pytest.mark.anyio
async def test_replay_then_recover_requeues_stale_claim(tmp_path: Path) -> None:
    # Simulate a server kill: create + claim, then a fresh store replays and
    # recovers the orphaned claim back to OPEN.
    store = _store(tmp_path)
    task = await store.create(_req())
    await store.claim_next("backend")
    await store.flush_buffer()

    fresh = _store(tmp_path)
    fresh.replay_jsonl()
    # The replayed task is CLAIMED with no live agent.
    replayed = fresh.get_task(task.id)
    assert replayed is not None
    assert replayed.status is TaskStatus.CLAIMED
    assert fresh.recover_stale_claimed_tasks() == 1
    recovered_after_reset = fresh.get_task(task.id)
    assert recovered_after_reset is not None
    assert recovered_after_reset.status is TaskStatus.OPEN
