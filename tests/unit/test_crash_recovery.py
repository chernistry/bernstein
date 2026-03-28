"""Tests for crash recovery — orphaned agent detection and resume.

Tests cover:
- OrchestratorConfig has recovery fields with defaults
- spawn_for_resume uses a preserved worktree path
- spawn_for_resume prompt includes crash context and changed files
- max_crash_retries is enforced (task fails after limit)
- restart strategy cleans up worktree and spawns fresh
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from bernstein.adapters.base import CLIAdapter, SpawnResult
from bernstein.core.models import (
    AgentSession,
    Complexity,
    ModelConfig,
    OrchestratorConfig,
    Scope,
    Task,
    TaskStatus,
    TaskType,
)
from bernstein.core.orchestrator import Orchestrator
from bernstein.core.spawner import AgentSpawner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    id: str = "T-001",
    role: str = "backend",
    title: str = "Implement feature X",
    description: str = "Write the code.",
    status: str = "open",
) -> Task:
    return Task(
        id=id,
        title=title,
        description=description,
        role=role,
        scope=Scope.MEDIUM,
        complexity=Complexity.MEDIUM,
        status=TaskStatus(status),
        task_type=TaskType.STANDARD,
    )


def _task_as_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "role": task.role,
        "priority": task.priority,
        "scope": task.scope.value,
        "complexity": task.complexity.value,
        "estimated_minutes": task.estimated_minutes,
        "status": task.status.value,
        "depends_on": task.depends_on,
        "owned_files": task.owned_files,
        "assigned_agent": task.assigned_agent,
        "result_summary": task.result_summary,
        "task_type": task.task_type.value,
        "completion_signals": [],
        "progress_log": [],
        "version": 1,
        "mcp_servers": [],
    }


def _mock_adapter(pid: int = 42) -> CLIAdapter:
    adapter = MagicMock(spec=CLIAdapter)
    adapter.spawn.return_value = SpawnResult(pid=pid, proc=None, log_path=None)
    adapter.is_alive.return_value = True
    return adapter


def _make_spawner(tmp_path: Path, adapter: CLIAdapter | None = None) -> AgentSpawner:
    if adapter is None:
        adapter = _mock_adapter()
    return AgentSpawner(
        adapter=adapter,
        templates_dir=tmp_path / "templates",
        workdir=tmp_path,
    )


# ---------------------------------------------------------------------------
# 1. Config defaults
# ---------------------------------------------------------------------------


def test_orchestrator_config_has_recovery_field():
    """OrchestratorConfig should expose recovery strategy with default 'resume'."""
    config = OrchestratorConfig()
    assert config.recovery == "resume"


def test_orchestrator_config_has_max_crash_retries_field():
    """OrchestratorConfig should expose max_crash_retries with default 2."""
    config = OrchestratorConfig()
    assert config.max_crash_retries == 2


def test_orchestrator_config_recovery_accepts_all_strategies():
    """recovery should accept 'resume', 'restart', and 'escalate'."""
    for strategy in ("resume", "restart", "escalate"):
        cfg = OrchestratorConfig(recovery=strategy)
        assert cfg.recovery == strategy


# ---------------------------------------------------------------------------
# 2. spawn_for_resume — uses provided worktree path
# ---------------------------------------------------------------------------


def test_spawn_for_resume_uses_provided_worktree(tmp_path: Path):
    """spawn_for_resume must spawn the agent in the preserved worktree directory."""
    adapter = _mock_adapter()
    spawner = _make_spawner(tmp_path, adapter)

    task = _make_task()
    worktree_path = tmp_path / ".sdd" / "worktrees" / "old-session"
    worktree_path.mkdir(parents=True)

    spawner.spawn_for_resume([task], worktree_path=worktree_path, changed_files=[])

    adapter.spawn.assert_called_once()
    call_kwargs = adapter.spawn.call_args
    assert call_kwargs.kwargs["workdir"] == worktree_path or call_kwargs.args[1] == worktree_path or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] == worktree_path
    )


def test_spawn_for_resume_does_not_create_new_worktree(tmp_path: Path):
    """spawn_for_resume should not create a new worktree; it reuses the preserved one."""
    adapter = _mock_adapter()
    spawner = _make_spawner(tmp_path, adapter)
    spawner._use_worktrees = True  # even with worktrees enabled, resume must not create a new one
    spawner._worktree_mgr = MagicMock()

    task = _make_task()
    worktree_path = tmp_path / ".sdd" / "worktrees" / "preserved"
    worktree_path.mkdir(parents=True)

    spawner.spawn_for_resume([task], worktree_path=worktree_path, changed_files=[])

    spawner._worktree_mgr.create.assert_not_called()


# ---------------------------------------------------------------------------
# 3. spawn_for_resume — prompt includes crash context
# ---------------------------------------------------------------------------


def test_spawn_for_resume_prompt_mentions_crash(tmp_path: Path):
    """spawn_for_resume prompt must tell the agent the previous agent crashed."""
    adapter = _mock_adapter()
    spawner = _make_spawner(tmp_path, adapter)

    task = _make_task()
    worktree_path = tmp_path / ".sdd" / "worktrees" / "crash-wt"
    worktree_path.mkdir(parents=True)

    spawner.spawn_for_resume([task], worktree_path=worktree_path, changed_files=[])

    prompt = adapter.spawn.call_args.kwargs.get("prompt") or adapter.spawn.call_args.args[0]
    assert "crashed" in prompt.lower() or "previous agent" in prompt.lower()


def test_spawn_for_resume_prompt_includes_changed_files(tmp_path: Path):
    """spawn_for_resume prompt must list the files changed by the crashed agent."""
    adapter = _mock_adapter()
    spawner = _make_spawner(tmp_path, adapter)

    task = _make_task()
    worktree_path = tmp_path / ".sdd" / "worktrees" / "crash-wt2"
    worktree_path.mkdir(parents=True)
    changed = ["src/foo.py", "tests/test_foo.py"]

    spawner.spawn_for_resume([task], worktree_path=worktree_path, changed_files=changed)

    prompt = adapter.spawn.call_args.kwargs.get("prompt") or adapter.spawn.call_args.args[0]
    for f in changed:
        assert f in prompt


# ---------------------------------------------------------------------------
# 4. Orchestrator crash tracking — crash count incremented
# ---------------------------------------------------------------------------


def _make_orchestrator(
    tmp_path: Path,
    *,
    recovery: str = "resume",
    max_crash_retries: int = 2,
    tasks: list[dict] | None = None,
) -> tuple[Orchestrator, list[dict]]:
    """Build an orchestrator with an httpx mock transport."""
    task_store: list[dict] = list(tasks or [])

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/tasks":
            return httpx.Response(200, json=task_store)
        if request.method == "GET" and "/tasks/" in request.url.path:
            tid = request.url.path.split("/tasks/")[1]
            for t in task_store:
                if t["id"] == tid:
                    return httpx.Response(200, json=t)
            return httpx.Response(404, json={"detail": "not found"})
        if request.method == "POST" and request.url.path == "/tasks":
            body = json.loads(request.content)
            new_task = {**body, "id": f"retry-{len(task_store)}", "completion_signals": [], "progress_log": [], "version": 1, "mcp_servers": []}
            task_store.append(new_task)
            return httpx.Response(201, json=new_task)
        if request.method == "POST" and "/fail" in request.url.path:
            tid = request.url.path.split("/tasks/")[1].replace("/fail", "")
            for t in task_store:
                if t["id"] == tid:
                    t["status"] = "failed"
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST" and "/complete" in request.url.path:
            tid = request.url.path.split("/tasks/")[1].replace("/complete", "")
            for t in task_store:
                if t["id"] == tid:
                    t["status"] = "done"
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "unhandled"})

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)

    config = OrchestratorConfig(
        max_agents=2,
        poll_interval_s=1,
        server_url="http://localhost:8052",
        evolution_enabled=False,
        recovery=recovery,
        max_crash_retries=max_crash_retries,
    )
    adapter = _mock_adapter()
    spawner = _make_spawner(tmp_path, adapter)
    orch = Orchestrator(config=config, spawner=spawner, workdir=tmp_path, client=client)
    return orch, task_store


def test_orchestrator_increments_crash_count_on_agent_death(tmp_path: Path):
    """_crash_counts[task_id] should be 1 after the first agent crash."""
    task = _make_task(id="T-crash-1", status="claimed")
    orch, _ = _make_orchestrator(
        tmp_path,
        tasks=[_task_as_dict(task)],
    )

    session = AgentSession(id="s-crashed", role="backend", pid=1234, task_ids=["T-crash-1"])
    session.status = "working"  # alive from orchestrator's perspective
    orch._agents["s-crashed"] = session
    orch._task_to_session["T-crash-1"] = "s-crashed"

    # check_alive returns False → simulates crash detection
    orch._spawner.check_alive = MagicMock(return_value=False)  # type: ignore[assignment]

    orch._refresh_agent_states({"claimed": [task], "open": [], "done": [], "failed": []})

    assert orch._crash_counts.get("T-crash-1", 0) == 1


# ---------------------------------------------------------------------------
# 5. max_crash_retries enforced — task fails after limit
# ---------------------------------------------------------------------------


def test_orchestrator_fails_task_after_max_crash_retries(tmp_path: Path):
    """Task should be failed permanently once _crash_counts[task_id] >= max_crash_retries."""
    task = _make_task(id="T-exhaust", status="claimed")
    task_dict = _task_as_dict(task)
    # Pre-seed the retry marker so _retry_or_fail_task sees max retries exceeded
    task_dict["description"] = f"[retry:{2}] Write the code."

    orch, task_store = _make_orchestrator(
        tmp_path,
        max_crash_retries=2,
        tasks=[task_dict],
    )

    session = AgentSession(id="s-exhaust", role="backend", pid=999, task_ids=["T-exhaust"])
    session.status = "working"
    orch._agents["s-exhaust"] = session
    orch._task_to_session["T-exhaust"] = "s-exhaust"
    # Pre-set crash count to max so next crash triggers permanent failure
    orch._crash_counts["T-exhaust"] = 2

    # check_alive returns False → triggers crash detection
    orch._spawner.check_alive = MagicMock(return_value=False)  # type: ignore[assignment]

    orch._refresh_agent_states({"claimed": [Task.from_dict(task_dict)], "open": [], "done": [], "failed": []})

    # Task should now be failed in the store
    statuses = {t["id"]: t["status"] for t in task_store}
    assert statuses.get("T-exhaust") == "failed"


# ---------------------------------------------------------------------------
# 6. resume strategy — spawn_for_resume called with preserved worktree
# ---------------------------------------------------------------------------


def test_orchestrator_calls_spawn_for_resume_when_worktree_preserved(tmp_path: Path):
    """With recovery='resume', after a crash the orchestrator should call
    spawn_for_resume with the crashed agent's worktree path."""
    task = _make_task(id="T-resume", status="open")
    orch, task_store = _make_orchestrator(
        tmp_path,
        recovery="resume",
        tasks=[_task_as_dict(task)],
    )

    # Inject a preserved worktree for this task
    wt_path = tmp_path / ".sdd" / "worktrees" / "old-wt"
    wt_path.mkdir(parents=True)
    orch._preserved_worktrees["T-resume"] = wt_path

    # Patch spawn_for_resume so we can assert it's called
    orch._spawner.spawn_for_resume = MagicMock(  # type: ignore[method-assign]
        return_value=AgentSession(
            id="s-resume", role="backend", pid=123, task_ids=["T-resume"]
        )
    )

    # Mock spawn_for_tasks to raise (should NOT be called in resume path)
    original_spawn = orch._spawner.spawn_for_tasks
    orch._spawner.spawn_for_tasks = MagicMock(side_effect=AssertionError("spawn_for_tasks must not be called"))  # type: ignore[method-assign]

    # Tick should pick up the open task and call spawn_for_resume
    orch.tick()

    orch._spawner.spawn_for_resume.assert_called_once()
    call_kwargs = orch._spawner.spawn_for_resume.call_args
    # The worktree_path argument should be the preserved one
    assert call_kwargs.kwargs.get("worktree_path") == wt_path or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] == wt_path
    )


# ---------------------------------------------------------------------------
# 7. restart strategy — no worktree preserved
# ---------------------------------------------------------------------------


def test_orchestrator_does_not_preserve_worktree_on_restart(tmp_path: Path):
    """With recovery='restart', the orchestrator should NOT put a worktree in
    _preserved_worktrees after a crash."""
    task = _make_task(id="T-restart", status="claimed")
    orch, _ = _make_orchestrator(
        tmp_path,
        recovery="restart",
        tasks=[_task_as_dict(task)],
    )

    session = AgentSession(id="s-restart", role="backend", pid=555, task_ids=["T-restart"])
    session.status = "working"
    orch._agents["s-restart"] = session
    orch._task_to_session["T-restart"] = "s-restart"

    # Simulate a preserved worktree in the spawner
    wt_path = tmp_path / ".sdd" / "worktrees" / "s-restart"
    wt_path.mkdir(parents=True)
    orch._spawner._worktree_paths["s-restart"] = wt_path  # type: ignore[attr-defined]

    # check_alive returns False → triggers crash detection
    orch._spawner.check_alive = MagicMock(return_value=False)  # type: ignore[assignment]

    orch._refresh_agent_states({"claimed": [task], "open": [], "done": [], "failed": []})

    assert "T-restart" not in orch._preserved_worktrees
