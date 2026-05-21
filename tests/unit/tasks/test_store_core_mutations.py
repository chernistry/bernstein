"""Behavioral tests for TaskStore mutation transitions and progress tracking.

Covers ``block`` (FSM precondition + KeyError), ``fail``, ``wait_for_subtasks``,
``claim_by_id`` optimistic-locking / role-locking / not-open guards,
``force_claim`` requeue + terminal guard, ``add_progress``, and the snapshot
ring buffer (``add_snapshot`` / ``get_snapshots``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bernstein.core.tasks.lifecycle import IllegalTransitionError
from bernstein.core.tasks.models import TaskStatus
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


# ---------------------------------------------------------------------------
# block
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_block_open_task_is_illegal_transition(tmp_path: Path) -> None:
    # OPEN -> BLOCKED is not in the FSM table; block must be reached from
    # a claimed/in-progress state.
    store = _store(tmp_path)
    task = await store.create(_req())
    with pytest.raises(IllegalTransitionError):
        await store.block(task.id, "needs human")


@pytest.mark.anyio
async def test_block_claimed_task_succeeds(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    await store.claim_by_id(task.id)
    blocked = await store.block(task.id, "needs human input")
    assert blocked.status is TaskStatus.BLOCKED
    assert blocked.result_summary == "needs human input"


@pytest.mark.anyio
async def test_block_unknown_task_raises_keyerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        await store.block("ghost", "x")


# ---------------------------------------------------------------------------
# fail
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fail_moves_to_failed_and_stamps_completion(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    failed = await store.fail(task.id, "compilation error")
    assert failed.status is TaskStatus.FAILED
    assert failed.result_summary == "compilation error"
    assert failed.completed_at is not None


@pytest.mark.anyio
async def test_fail_unknown_task_raises_keyerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        await store.fail("ghost", "x")


# ---------------------------------------------------------------------------
# wait_for_subtasks
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_subtasks_sets_waiting_status_and_timestamp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    waiting = await store.wait_for_subtasks(task.id, 3)
    assert waiting.status is TaskStatus.WAITING_FOR_SUBTASKS
    assert waiting.subtask_wait_started_at is not None
    assert "3 subtasks" in (waiting.result_summary or "")


# ---------------------------------------------------------------------------
# claim_by_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_claim_by_id_success_bumps_version(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    version_before = task.version
    claimed = await store.claim_by_id(task.id, version_before)
    assert claimed.status is TaskStatus.CLAIMED
    assert claimed.version == version_before + 1


@pytest.mark.anyio
async def test_claim_by_id_version_conflict_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    with pytest.raises(ValueError, match="Version conflict"):
        await store.claim_by_id(task.id, expected_version=999)


@pytest.mark.anyio
async def test_claim_by_id_role_mismatch_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req(role="backend"))
    with pytest.raises(ValueError, match="role mismatch"):
        await store.claim_by_id(task.id, agent_role="qa")


@pytest.mark.anyio
async def test_claim_by_id_already_claimed_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    await store.claim_by_id(task.id)
    with pytest.raises(ValueError, match="not open"):
        await store.claim_by_id(task.id)


@pytest.mark.anyio
async def test_claim_by_id_records_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    claimed = await store.claim_by_id(task.id, claimed_by_session="coord-1")
    assert claimed.claimed_by_session == "coord-1"


@pytest.mark.anyio
async def test_claim_by_id_unknown_raises_keyerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        await store.claim_by_id("ghost")


# ---------------------------------------------------------------------------
# force_claim
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_force_claim_open_task_sets_priority_zero(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req(priority=3))
    forced = await store.force_claim(task.id)
    assert forced.status is TaskStatus.OPEN
    assert forced.priority == 0


@pytest.mark.anyio
async def test_force_claim_requeues_claimed_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    await store.claim_by_id(task.id, claimed_by_session="coord-1")
    forced = await store.force_claim(task.id)
    assert forced.status is TaskStatus.OPEN
    assert forced.priority == 0
    assert forced.claimed_by_session is None
    assert forced.claimed_at is None


@pytest.mark.anyio
async def test_force_claim_terminal_task_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    await store.fail(task.id, "broke")
    with pytest.raises(ValueError, match="terminal state"):
        await store.force_claim(task.id)


# ---------------------------------------------------------------------------
# add_progress
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_progress_appends_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    updated = await store.add_progress(task.id, "halfway there", 50)
    assert len(updated.progress_log) == 1
    assert updated.progress_log[0]["message"] == "halfway there"
    assert updated.progress_log[0]["percent"] == 50


@pytest.mark.anyio
async def test_add_progress_accumulates_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    await store.add_progress(task.id, "step 1", 25)
    updated = await store.add_progress(task.id, "step 2", 50)
    assert len(updated.progress_log) == 2


@pytest.mark.anyio
async def test_add_progress_unknown_task_raises_keyerror(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        await store.add_progress("ghost", "x", 10)


# ---------------------------------------------------------------------------
# add_snapshot / get_snapshots ring buffer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_snapshot_returns_snapshot_and_stores_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    snap = store.add_snapshot(task.id, files_changed=2, tests_passing=5, errors=0, last_file="a.py")
    assert snap.files_changed == 2
    stored = store.get_snapshots(task.id)
    assert len(stored) == 1
    assert stored[0].last_file == "a.py"


@pytest.mark.anyio
async def test_get_snapshots_returns_in_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    store.add_snapshot(task.id, files_changed=1, tests_passing=0, errors=0, last_file="a.py")
    store.add_snapshot(task.id, files_changed=3, tests_passing=0, errors=0, last_file="b.py")
    snaps = store.get_snapshots(task.id)
    assert [s.files_changed for s in snaps] == [1, 3]


@pytest.mark.anyio
async def test_snapshot_ring_buffer_keeps_last_ten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = await store.create(_req())
    for i in range(15):
        store.add_snapshot(task.id, files_changed=i, tests_passing=0, errors=0, last_file=f"f{i}.py")
    snaps = store.get_snapshots(task.id)
    assert len(snaps) == 10
    # The oldest five (0-4) are evicted; the buffer holds 5..14.
    assert snaps[0].files_changed == 5
    assert snaps[-1].files_changed == 14


@pytest.mark.anyio
async def test_get_snapshots_unknown_task_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_snapshots("ghost") == []
