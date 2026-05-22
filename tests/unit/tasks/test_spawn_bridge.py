"""Behavioral tests for ``task_spawn_bridge`` conflict + decomposition wiring.

These exercise the HTTP-backed task-creation helpers with a stubbed
``httpx.Client`` so the POST body shape, priority bump, success-path return
value, side-effect mutation, and HTTP-error degradation are all observable.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx

from bernstein.core.tasks.models import Scope, Task
from bernstein.core.tasks.task_spawn_bridge import (
    auto_decompose_task,
    create_conflict_resolution_task,
    should_auto_decompose,
)


def _task(**overrides: object) -> Task:
    base: dict[str, object] = {
        "id": "abc123",
        "title": "Big task",
        "description": "A large body of work",
        "role": "backend",
        "priority": 2,
    }
    base.update(overrides)
    return Task(**base)  # type: ignore[arg-type]


def _ok_client(returned_id: str) -> MagicMock:
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {"id": returned_id}
    resp.raise_for_status.return_value = None
    client.post.return_value = resp
    return client


def _last_post_body(client: MagicMock) -> dict[str, Any]:
    _, kwargs = client.post.call_args
    return kwargs["json"]


# ---------------------------------------------------------------------------
# should_auto_decompose (separate copy living in the bridge module)
# ---------------------------------------------------------------------------


def test_bridge_should_auto_decompose_requires_force_parallel() -> None:
    assert should_auto_decompose(_task(scope=Scope.LARGE), set(), force_parallel=False) is False


def test_bridge_should_auto_decompose_large_when_forced() -> None:
    assert should_auto_decompose(_task(scope=Scope.LARGE), set(), force_parallel=True) is True


def test_bridge_should_auto_decompose_legacy_retry_prefix() -> None:
    task = _task(scope=Scope.SMALL, retry_count=0, title="[RETRY 3] redo")
    assert should_auto_decompose(task, set(), force_parallel=True) is True


def test_bridge_should_auto_decompose_skips_decompose_prefix() -> None:
    task = _task(scope=Scope.LARGE, title="[DECOMPOSE] already planning")
    assert should_auto_decompose(task, set(), force_parallel=True) is False


# ---------------------------------------------------------------------------
# create_conflict_resolution_task
# ---------------------------------------------------------------------------


def test_create_conflict_task_posts_resolver_body_and_returns_id() -> None:
    client = _ok_client("resolver-9")
    rid = create_conflict_resolution_task(
        _task(),
        ["src/a.py", "src/b.py"],
        client=client,
        server_url="http://srv",
        session_id="sess-1",
    )
    assert rid == "resolver-9"
    url_args, _ = client.post.call_args
    assert url_args[0] == "http://srv/tasks"
    body = _last_post_body(client)
    assert body["role"] == "resolver"
    assert body["title"] == "[CONFLICT] Big task"
    assert body["owned_files"] == ["src/a.py", "src/b.py"]


def test_create_conflict_task_bumps_priority_one_step() -> None:
    client = _ok_client("r1")
    create_conflict_resolution_task(
        _task(priority=3),
        ["src/a.py"],
        client=client,
        server_url="http://srv",
        session_id="s",
    )
    assert _last_post_body(client)["priority"] == 2  # 3 -> 2


def test_create_conflict_task_priority_floor_is_one() -> None:
    client = _ok_client("r1")
    create_conflict_resolution_task(
        _task(priority=1),
        ["src/a.py"],
        client=client,
        server_url="http://srv",
        session_id="s",
    )
    assert _last_post_body(client)["priority"] == 1  # max(1, 1-1) == 1


def test_create_conflict_task_embeds_files_in_description() -> None:
    client = _ok_client("r1")
    create_conflict_resolution_task(
        _task(),
        ["src/x.py", "src/y.py"],
        client=client,
        server_url="http://srv",
        session_id="s",
    )
    desc = _last_post_body(client)["description"]
    assert "- src/x.py" in desc
    assert "- src/y.py" in desc


def test_create_conflict_task_returns_none_on_http_error() -> None:
    client = MagicMock()
    client.post.side_effect = httpx.HTTPError("connection reset")
    rid = create_conflict_resolution_task(
        _task(),
        ["src/a.py"],
        client=client,
        server_url="http://srv",
        session_id="s",
    )
    assert rid is None


def test_create_conflict_task_defaults_missing_id_to_question_mark() -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {}  # server omitted "id"
    resp.raise_for_status.return_value = None
    client.post.return_value = resp
    rid = create_conflict_resolution_task(
        _task(),
        ["src/a.py"],
        client=client,
        server_url="http://srv",
        session_id="s",
    )
    assert rid == "?"


# ---------------------------------------------------------------------------
# auto_decompose_task (no-workdir planner fallback path)
# ---------------------------------------------------------------------------


def test_auto_decompose_creates_planner_task_without_workdir() -> None:
    client = _ok_client("planner-1")
    seen: set[str] = set()
    auto_decompose_task(
        _task(),
        client=client,
        server_url="http://srv",
        decomposed_task_ids=seen,
        workdir=None,
    )
    body = _last_post_body(client)
    assert body["role"] == "manager"
    assert body["title"] == "[DECOMPOSE] Big task"
    assert body["model"] == "haiku"
    assert body["effort"] == "high"


def test_auto_decompose_marks_task_decomposed_on_success() -> None:
    client = _ok_client("planner-1")
    seen: set[str] = set()
    auto_decompose_task(
        _task(id="task-77"),
        client=client,
        server_url="http://srv",
        decomposed_task_ids=seen,
        workdir=None,
    )
    assert "task-77" in seen


def test_auto_decompose_bumps_planner_priority() -> None:
    client = _ok_client("planner-1")
    auto_decompose_task(
        _task(priority=3),
        client=client,
        server_url="http://srv",
        decomposed_task_ids=set(),
        workdir=None,
    )
    assert _last_post_body(client)["priority"] == 2  # 3 -> 2


def test_auto_decompose_embeds_subtask_marker_in_description() -> None:
    client = _ok_client("planner-1")
    auto_decompose_task(
        _task(id="parent-5"),
        client=client,
        server_url="http://srv",
        decomposed_task_ids=set(),
        workdir=None,
    )
    desc = _last_post_body(client)["description"]
    assert "[subtask of parent-5]" in desc


def test_auto_decompose_http_error_does_not_mark_decomposed() -> None:
    client = MagicMock()
    client.post.side_effect = httpx.HTTPError("503")
    seen: set[str] = set()
    auto_decompose_task(
        _task(id="task-err"),
        client=client,
        server_url="http://srv",
        decomposed_task_ids=seen,
        workdir=None,
    )
    # The failed POST must not record the task as decomposed.
    assert "task-err" not in seen
