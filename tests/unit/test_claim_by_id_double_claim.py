"""Regression tests for audit-014: claim_by_id must reject double-claim.

Before the fix, ``TaskStore.claim_by_id`` silently returned the unchanged
Task when it was already CLAIMED (or in any non-OPEN state), which let two
agents each receive a "successful" claim for the same task and run in
parallel on the same files.  The fix makes ``claim_by_id`` raise
``ValueError`` (matching the CAS / role-mismatch contract) so the HTTP
layer can return 409 Conflict to the second caller.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bernstein.core.models import TaskStatus
from bernstein.core.task_store import TaskStore


def _task_request(
    *,
    title: str = "Implement parser",
    description: str = "Write the parser module.",
    role: str = "backend",
    priority: int = 1,
    scope: str = "medium",
    complexity: str = "medium",
    depends_on: list[str] | None = None,
) -> Any:
    """Build a TaskCreate-shaped request object for TaskStore.create."""
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
    )


@pytest.mark.anyio
async def test_claim_by_id_rejects_second_claim_when_already_claimed(
    tmp_path: Path,
) -> None:
    """audit-014: the second claim on an already-claimed task must raise.

    Previously claim_by_id silently returned the unchanged Task, enabling
    two agents to believe they both owned the same work.
    """
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    task = await store.create(_task_request())

    first = await store.claim_by_id(task.id, claimed_by_session="agent-alpha")
    assert first.status == TaskStatus.CLAIMED
    assert first.claimed_by_session == "agent-alpha"
    first_version = first.version

    # A second agent must not be able to silently "claim" the same task.
    with pytest.raises(ValueError, match="not open"):
        await store.claim_by_id(task.id, claimed_by_session="agent-beta")

    # Task must remain owned by the original claimant and untouched.
    stored = store.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.CLAIMED
    assert stored.claimed_by_session == "agent-alpha"
    assert stored.version == first_version


@pytest.mark.anyio
async def test_claim_by_id_rejects_claim_on_terminal_task(tmp_path: Path) -> None:
    """A completed task must not be re-claimable through claim_by_id."""
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    task = await store.create(_task_request())

    await store.claim_by_id(task.id, claimed_by_session="agent-alpha")
    await store.complete(task.id, "done")

    with pytest.raises(ValueError, match="not open"):
        await store.claim_by_id(task.id, claimed_by_session="agent-beta")

    stored = store.get_task(task.id)
    assert stored is not None
    assert stored.status == TaskStatus.DONE


@pytest.mark.anyio
async def test_claim_by_id_double_claim_error_names_status(tmp_path: Path) -> None:
    """The raised error should surface the current task status for diagnostics."""
    store = TaskStore(tmp_path / "runtime" / "tasks.jsonl")
    task = await store.create(_task_request())
    await store.claim_by_id(task.id, claimed_by_session="agent-alpha")

    with pytest.raises(ValueError) as exc_info:
        await store.claim_by_id(task.id, claimed_by_session="agent-beta")

    message = str(exc_info.value)
    assert task.id in message
    assert "claimed" in message
