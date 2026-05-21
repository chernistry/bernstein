"""Unit tests for TaskStore.abandon and the ABANDONED transition table (#1350)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.tasks.abandon import AbandonmentLedger, AbandonReason
from bernstein.core.tasks.lifecycle import (
    TASK_TRANSITIONS,
    IllegalTransitionError,
    transition_task,
)
from bernstein.core.tasks.models import Task, TaskStatus
from bernstein.core.tasks.task_store_core import TaskStore

# ---------------------------------------------------------------------------
# Lifecycle transition table - abandon-related entries
# ---------------------------------------------------------------------------


class TestAbandonTransitions:
    @pytest.mark.parametrize(
        "src",
        [
            TaskStatus.OPEN,
            TaskStatus.CLAIMED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.WAITING_FOR_SUBTASKS,
            TaskStatus.BLOCKED,
            TaskStatus.ORPHANED,
        ],
    )
    def test_legal_source_states(self, src: TaskStatus) -> None:
        assert (src, TaskStatus.ABANDONED) in TASK_TRANSITIONS

    @pytest.mark.parametrize(
        "src",
        [
            TaskStatus.DONE,
            TaskStatus.CLOSED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.ABANDONED,
        ],
    )
    def test_abandon_blocked_from_terminal_states(self, src: TaskStatus) -> None:
        assert (src, TaskStatus.ABANDONED) not in TASK_TRANSITIONS

    def test_abandoned_to_completed_is_illegal(self) -> None:
        # Critical invariant: abandon→completed must never appear.
        for target in (TaskStatus.DONE, TaskStatus.CLOSED):
            assert (TaskStatus.ABANDONED, target) not in TASK_TRANSITIONS

    def test_blocked_by_abandon_recovery_paths(self) -> None:
        assert (TaskStatus.BLOCKED_BY_ABANDON, TaskStatus.OPEN) in TASK_TRANSITIONS
        assert (TaskStatus.BLOCKED_BY_ABANDON, TaskStatus.CANCELLED) in TASK_TRANSITIONS
        assert (TaskStatus.BLOCKED_BY_ABANDON, TaskStatus.ABANDONED) in TASK_TRANSITIONS

    def test_transition_task_to_abandoned_succeeds(self) -> None:
        task = Task(id="T-1", title="t", description="d", role="backend", status=TaskStatus.IN_PROGRESS)
        event = transition_task(task, TaskStatus.ABANDONED, actor="test", reason="out_of_scope")
        assert task.status is TaskStatus.ABANDONED
        assert event.to_status == "abandoned"

    def test_transition_from_abandoned_is_rejected(self) -> None:
        task = Task(id="T-1", title="t", description="d", role="backend", status=TaskStatus.ABANDONED)
        with pytest.raises(IllegalTransitionError):
            transition_task(task, TaskStatus.OPEN)


# ---------------------------------------------------------------------------
# TaskStore.abandon
# ---------------------------------------------------------------------------


def _store(tmp_path: Path) -> TaskStore:
    """Build a TaskStore rooted at *tmp_path* with the expected layout."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return TaskStore(runtime / "tasks.jsonl", archive_path=tmp_path / "archive" / "tasks.jsonl")


async def _create(store: TaskStore, **overrides: Any) -> Task:
    """Insert a Task into *store* via the internal index (bypasses create())."""
    base: dict[str, Any] = {
        "id": overrides.pop("id", "T-1"),
        "title": "t",
        "description": "d",
        "role": "backend",
        "status": TaskStatus.IN_PROGRESS,
    }
    base.update(overrides)
    task = Task(**base)
    store._tasks[task.id] = task  # type: ignore[attr-defined]
    store._index_add(task)  # type: ignore[attr-defined]
    return task


@pytest.mark.asyncio
class TestTaskStoreAbandon:
    async def test_abandon_marks_status_abandoned(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        result = await store.abandon("T-1", "out_of_scope", "spec mismatch")
        assert result.status is TaskStatus.ABANDONED

    async def test_abandon_writes_ledger_row(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, role="qa")
        await store.abandon(
            "T-1",
            AbandonReason.BUDGET_EXCEEDED.value,
            "cost cap hit",
            adapter="claude",
            agent_id="sess-1",
            cost_to_date_usd=2.5,
        )
        ledger = AbandonmentLedger(tmp_path)
        rows = ledger.read_all()
        assert len(rows) == 1
        row = rows[0]
        assert row.task_id == "T-1"
        assert row.reason is AbandonReason.BUDGET_EXCEEDED
        assert row.role == "qa"
        assert row.adapter == "claude"
        assert row.cost_to_date_usd == pytest.approx(2.5)
        assert row.detail == "cost cap hit"

    async def test_abandon_unknown_task_raises_keyerror(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        with pytest.raises(KeyError):
            await store.abandon("nope", "other")

    async def test_abandon_unknown_reason_raises_valueerror(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        with pytest.raises(ValueError, match="Unknown AbandonReason"):
            await store.abandon("T-1", "not_a_real_reason")

    async def test_abandon_does_not_use_failed_status(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        task = await store.abandon("T-1", "other", "rationale")
        assert task.status is not TaskStatus.FAILED
        assert task.status is TaskStatus.ABANDONED

    async def test_abandon_sets_completed_at(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        task = await store.abandon("T-1", "other")
        assert task.completed_at is not None
        assert task.completed_at > 0

    async def test_abandon_bumps_version(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        original = await _create(store)
        original_version = original.version
        task = await store.abandon("T-1", "other")
        assert task.version == original_version + 1

    async def test_abandon_persists_to_jsonl(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        await store.abandon("T-1", "out_of_scope")
        await store.flush_buffer()
        body = (tmp_path / "runtime" / "tasks.jsonl").read_text(encoding="utf-8")
        assert "abandoned" in body

    async def test_abandon_records_terminal_reason(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        task = await store.abandon("T-1", "capability_mismatch")
        assert task.terminal_reason == "capability_mismatch"

    async def test_abandon_result_summary_uses_detail_when_present(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        task = await store.abandon("T-1", "other", "the rationale text")
        assert task.result_summary == "the rationale text"

    async def test_abandon_result_summary_falls_back_to_reason(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store)
        task = await store.abandon("T-1", "other")
        assert task.result_summary == "other"

    async def test_abandon_emits_one_ledger_row_per_call(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, id="T-1")
        await _create(store, id="T-2")
        await store.abandon("T-1", "other", "r1")
        await store.abandon("T-2", "out_of_scope", "r2")
        ledger = AbandonmentLedger(tmp_path)
        assert len(ledger.read_all()) == 2


@pytest.mark.asyncio
class TestAbandonCascadesToDownstream:
    async def test_open_downstream_moves_to_blocked_by_abandon(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, id="T-upstream")
        await _create(store, id="T-down", status=TaskStatus.OPEN, depends_on=["T-upstream"])
        await store.abandon("T-upstream", "other")
        assert store._tasks["T-down"].status is TaskStatus.BLOCKED_BY_ABANDON  # type: ignore[attr-defined]

    async def test_claimed_downstream_moves_to_blocked_by_abandon(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, id="T-upstream")
        await _create(store, id="T-down", status=TaskStatus.CLAIMED, depends_on=["T-upstream"])
        await store.abandon("T-upstream", "other")
        assert store._tasks["T-down"].status is TaskStatus.BLOCKED_BY_ABANDON  # type: ignore[attr-defined]

    async def test_done_downstream_is_not_touched(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, id="T-upstream")
        await _create(store, id="T-done", status=TaskStatus.DONE, depends_on=["T-upstream"])
        await store.abandon("T-upstream", "other")
        assert store._tasks["T-done"].status is TaskStatus.DONE  # type: ignore[attr-defined]

    async def test_unrelated_downstream_is_not_touched(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, id="T-upstream")
        await _create(store, id="T-other", status=TaskStatus.OPEN, depends_on=["unrelated"])
        await store.abandon("T-upstream", "other")
        assert store._tasks["T-other"].status is TaskStatus.OPEN  # type: ignore[attr-defined]

    async def test_multiple_downstream_consumers_all_cascade(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await _create(store, id="T-upstream")
        for i in range(3):
            await _create(store, id=f"T-down-{i}", status=TaskStatus.OPEN, depends_on=["T-upstream"])
        await store.abandon("T-upstream", "other")
        for i in range(3):
            assert store._tasks[f"T-down-{i}"].status is TaskStatus.BLOCKED_BY_ABANDON  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestAbandonLedgerFailureModes:
    async def test_ledger_write_failure_does_not_block_abandon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = _store(tmp_path)
        await _create(store)

        def fail(self: object, row: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(AbandonmentLedger, "append", fail)
        # Should swallow OSError and still mark the task abandoned.
        task = await store.abandon("T-1", "other")
        assert task.status is TaskStatus.ABANDONED


@pytest.mark.asyncio
class TestAbandonSequentialRace:
    async def test_concurrent_calls_serialise_under_lock(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        for i in range(5):
            await _create(store, id=f"T-{i}")

        async def abandon_one(task_id: str) -> None:
            await store.abandon(task_id, "other", f"rationale for {task_id}")

        await asyncio.gather(*(abandon_one(f"T-{i}") for i in range(5)))
        ledger = AbandonmentLedger(tmp_path)
        rows = ledger.read_all()
        assert len(rows) == 5
        assert {row.task_id for row in rows} == {f"T-{i}" for i in range(5)}
