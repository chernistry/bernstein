"""Regression tests for per-step ``cli`` / ``model`` routing in plan-driven runs.

When a plan declares heterogeneous per-step ``cli`` and ``model`` directives,
each (cli, model) combination must spawn its own agent.  Two earlier bugs
collapsed everything onto the default adapter + ``sonnet``:

1. ``_post_task_to_server`` forwarded ``task.cli`` but silently dropped
   ``task.model`` / ``task.effort``, so the task arrived at the orchestrator
   with no model hint.

2. ``_select_batch_config`` short-circuited to the role's ``config.yaml``
   default before checking ``task.model``, so even a correctly-posted task
   was overridden by the role default.

3. ``_groups_can_merge`` and ``_can_merge_batches`` only inspected ``model``,
   never ``cli`` - so two tasks with the same role but different adapters
   would be merged into a single batch and the second adapter dropped.

This module guards all three regressions.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import bernstein.core.planner as planner
import yaml
from bernstein.core.models import Complexity, Scope, Task, TaskStatus, TaskType
from bernstein.core.plan_loader import load_plan_from_yaml
from bernstein.core.tick_pipeline import group_by_role

import bernstein.core.orchestration.manager as orch_manager
from bernstein.core.agents.spawner_warm_pool import _select_batch_config
from bernstein.core.tasks.task_grouping import compact_small_tasks

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _CapturingClient:
    """Captures every POST body so we can assert routing fields survive."""

    def __init__(self) -> None:
        self.posts: list[dict[str, object]] = []
        self._next_id = 0

    async def post(self, url: str, json: dict[str, object]) -> _FakeResponse:
        await asyncio.sleep(0)
        self.posts.append({"url": url, "json": json})
        self._next_id += 1
        return _FakeResponse({"id": f"server-task-{self._next_id}"})


def _make_task(
    task_id: str,
    *,
    role: str = "qa",
    cli: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        title=f"Task {task_id}",
        description="",
        role=role,
        priority=2,
        scope=Scope.MEDIUM,
        complexity=Complexity.MEDIUM,
        status=TaskStatus.OPEN,
        task_type=TaskType.STANDARD,
        cli=cli,
        model=model,
        effort=effort,
    )


# ---------------------------------------------------------------------------
# Bug #1: _post_task_to_server must forward model + effort + cli
# ---------------------------------------------------------------------------


def test_planner_post_task_forwards_per_step_cli_and_model() -> None:
    """``planner._post_task_to_server`` must forward ``cli``, ``model``, and ``effort``."""
    client = _CapturingClient()
    task = _make_task("t1", cli="gemini", model="flash", effort="low")

    asyncio.run(planner._post_task_to_server(cast("Any", client), "http://server", task))

    body = cast("dict[str, object]", client.posts[0]["json"])
    assert body["cli"] == "gemini"
    assert body["model"] == "flash"
    assert body["effort"] == "low"


def test_manager_post_task_forwards_per_step_cli_and_model() -> None:
    """The duplicate ``manager._post_task_to_server`` must also forward routing fields."""
    client = _CapturingClient()
    task = _make_task("t1", cli="claude", model="haiku")

    asyncio.run(orch_manager._post_task_to_server(cast("Any", client), "http://server", task))

    body = cast("dict[str, object]", client.posts[0]["json"])
    assert body["cli"] == "claude"
    assert body["model"] == "haiku"


def test_post_task_omits_routing_fields_when_unset() -> None:
    """Plain plans without per-step routing must NOT introduce empty fields."""
    client = _CapturingClient()
    task = _make_task("t1", cli=None, model=None, effort=None)

    asyncio.run(planner._post_task_to_server(cast("Any", client), "http://server", task))

    body = cast("dict[str, object]", client.posts[0]["json"])
    assert "cli" not in body
    assert "model" not in body
    assert "effort" not in body


# ---------------------------------------------------------------------------
# Bug #2: _select_batch_config must respect task.model over role config.yaml
# ---------------------------------------------------------------------------


def test_select_batch_config_honours_per_task_model_over_role_default(tmp_path: Path) -> None:
    """A role's ``config.yaml`` default must NOT override per-task ``model``."""
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    (qa_dir / "config.yaml").write_text("default_model: sonnet\ndefault_effort: high\n")

    task = _make_task("t1", role="qa", model="haiku", effort="low")
    config = _select_batch_config([task], templates_dir=tmp_path)

    assert config.model == "haiku"
    assert config.effort == "low"


def test_select_batch_config_falls_back_to_role_default_when_task_model_unset(tmp_path: Path) -> None:
    """When no task pins a model, the role's ``config.yaml`` still wins (no regression)."""
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    (qa_dir / "config.yaml").write_text("default_model: sonnet\ndefault_effort: high\n")

    task = _make_task("t1", role="qa", model=None, effort=None)
    config = _select_batch_config([task], templates_dir=tmp_path)

    assert config.model == "sonnet"
    assert config.effort == "high"


# ---------------------------------------------------------------------------
# Bug #3: group_by_role + compact_small_tasks must NOT merge across cli/model
# ---------------------------------------------------------------------------


def test_group_by_role_does_not_merge_distinct_clis() -> None:
    """Two same-role tasks with different ``cli`` must produce separate batches."""
    gemini_task = _make_task("t1", cli="gemini", model="flash")
    claude_task = _make_task("t2", cli="claude", model="haiku")

    batches = group_by_role([gemini_task, claude_task], max_per_batch=4)

    assert len(batches) == 2
    pairs = sorted((b[0].cli, b[0].model) for b in batches)
    assert pairs == [("claude", "haiku"), ("gemini", "flash")]


def test_group_by_role_does_not_merge_distinct_models_same_cli() -> None:
    """Two same-role tasks with same ``cli`` but different ``model`` must split."""
    a = _make_task("t1", cli="claude", model="haiku")
    b = _make_task("t2", cli="claude", model="sonnet")

    batches = group_by_role([a, b], max_per_batch=4)

    assert len(batches) == 2


def test_group_by_role_still_batches_when_routing_matches() -> None:
    """Tasks with identical routing hints can still share a batch (no regression)."""
    a = _make_task("t1", cli="claude", model="haiku")
    b = _make_task("t2", cli="claude", model="haiku")

    batches = group_by_role([a, b], max_per_batch=4)

    assert len(batches) == 1
    assert {t.id for t in batches[0]} == {"t1", "t2"}


def test_compact_small_tasks_does_not_merge_across_clis() -> None:
    """``compact_small_tasks`` must respect cli/model boundaries too."""
    a = _make_task("t1", cli="gemini", model="flash")
    a.complexity = Complexity.LOW
    a.scope = Scope.SMALL
    a.estimated_minutes = 5

    b = _make_task("t2", cli="claude", model="haiku")
    b.complexity = Complexity.LOW
    b.scope = Scope.SMALL
    b.estimated_minutes = 5

    compacted = compact_small_tasks([[a], [b]], max_per_batch=4)

    assert len(compacted) == 2


# ---------------------------------------------------------------------------
# End-to-end: playground-style plan parses + posts heterogeneous tasks
# ---------------------------------------------------------------------------


def test_playground_style_plan_posts_one_request_per_cli_model(tmp_path: Path) -> None:
    """A plan with two QA steps on different cli+model posts BOTH directives."""
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(
        yaml.dump(
            {
                "name": "Playground",
                "stages": [
                    {
                        "name": "write-and-run",
                        "parallel": True,
                        "steps": [
                            {
                                "id": "gemini-step",
                                "title": "Gemini step",
                                "role": "qa",
                                "cli": "gemini",
                                "model": "flash",
                            },
                            {
                                "id": "claude-step",
                                "title": "Claude step",
                                "role": "qa",
                                "cli": "claude",
                                "model": "haiku",
                            },
                        ],
                    }
                ],
            }
        )
    )

    tasks = load_plan_from_yaml(plan_file)
    assert len(tasks) == 2
    by_title = {t.title: t for t in tasks}
    assert by_title["Gemini step"].cli == "gemini"
    assert by_title["Gemini step"].model == "flash"
    assert by_title["Claude step"].cli == "claude"
    assert by_title["Claude step"].model == "haiku"

    client = _CapturingClient()

    async def _post_all() -> None:
        for t in tasks:
            await planner._post_task_to_server(cast("Any", client), "http://server", t)

    asyncio.run(_post_all())

    bodies = [cast("dict[str, object]", p["json"]) for p in client.posts]
    posted = sorted((b.get("cli"), b.get("model")) for b in bodies)
    assert posted == [("claude", "haiku"), ("gemini", "flash")]

    # And the orchestrator's batching layer must keep them in separate batches.
    batches = group_by_role(tasks, max_per_batch=4)
    assert len(batches) == 2, "heterogeneous cli/model must not share a batch"
    routings = sorted((b[0].cli, b[0].model) for b in batches)
    assert routings == [("claude", "haiku"), ("gemini", "flash")]
