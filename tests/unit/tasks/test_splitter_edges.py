"""Edge-case tests for ``TaskSplitter.split`` beyond the existing happy path.

The shipped suite covers ``should_split`` and the nominal split. These add the
out-of-range guard, LARGE-scope clamping, the parent-task-id wiring, and the
wait-for-subtasks parking call, all observable through a stubbed httpx client.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bernstein.core.tasks.models import Complexity, Scope, Task
from bernstein.core.tasks.task_splitter import TaskSplitter


def _parent(**overrides: object) -> Task:
    base: dict[str, object] = {
        "id": "parent-1",
        "title": "Parent",
        "description": "short",
        "role": "backend",
        "estimated_minutes": 90,
    }
    base.update(overrides)
    return Task(**base)  # type: ignore[arg-type]


def _subtask(idx: int, scope: Scope = Scope.SMALL) -> Task:
    return Task(
        id=f"s{idx}",
        title=f"Sub {idx}",
        description=f"do {idx}",
        role="backend",
        scope=scope,
        complexity=Complexity.MEDIUM,
        estimated_minutes=20,
        owned_files=[f"f{idx}.py"],
    )


class _Decomposer:
    def __init__(self, subtasks: list[Task]) -> None:
        self._subtasks = subtasks

    def decompose_sync(self, task: Task, *, min_subtasks: int = 2, max_subtasks: int = 5) -> list[Task]:
        return self._subtasks


def _ok_client() -> MagicMock:
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"id": "created"}
    resp.raise_for_status.return_value = None
    client.post.return_value = resp
    return client


def _task_posts(client: MagicMock) -> list[dict[str, object]]:
    return [call.kwargs["json"] for call in client.post.call_args_list if call.args[0].endswith("/tasks")]


def test_split_rejects_too_few_subtasks() -> None:
    splitter = TaskSplitter(client=MagicMock(), server_url="http://srv")
    with pytest.raises(ValueError, match="expected 2-5"):
        splitter.split(_parent(), _Decomposer([_subtask(1)]))


def test_split_rejects_too_many_subtasks() -> None:
    splitter = TaskSplitter(client=MagicMock(), server_url="http://srv")
    too_many = [_subtask(i) for i in range(6)]
    with pytest.raises(ValueError, match="expected 2-5"):
        splitter.split(_parent(), _Decomposer(too_many))


def test_split_clamps_large_subtask_scope_to_small() -> None:
    client = _ok_client()
    splitter = TaskSplitter(client=client, server_url="http://srv")
    splitter.split(_parent(), _Decomposer([_subtask(1, Scope.LARGE), _subtask(2, Scope.MEDIUM)]))
    bodies = _task_posts(client)
    assert bodies[0]["scope"] == "small"  # LARGE clamped down
    assert bodies[1]["scope"] == "medium"  # MEDIUM preserved


def test_split_sets_parent_task_id_on_each_subtask() -> None:
    client = _ok_client()
    splitter = TaskSplitter(client=client, server_url="http://srv")
    splitter.split(_parent(id="P9"), _Decomposer([_subtask(1), _subtask(2)]))
    for body in _task_posts(client):
        assert body["parent_task_id"] == "P9"


def test_split_returns_created_ids_and_parks_parent() -> None:
    client = _ok_client()
    splitter = TaskSplitter(client=client, server_url="http://srv")
    created = splitter.split(_parent(id="P9"), _Decomposer([_subtask(1), _subtask(2)]))
    assert created == ["created", "created"]
    wait_calls = [c for c in client.post.call_args_list if "wait-for-subtasks" in c.args[0]]
    assert len(wait_calls) == 1
    assert wait_calls[0].kwargs["json"]["subtask_count"] == 2


def test_split_caps_estimated_minutes_at_sixty() -> None:
    client = _ok_client()
    splitter = TaskSplitter(client=client, server_url="http://srv")
    big = _subtask(1)
    big.estimated_minutes = 999
    splitter.split(_parent(), _Decomposer([big, _subtask(2)]))
    assert _task_posts(client)[0]["estimated_minutes"] == 60
