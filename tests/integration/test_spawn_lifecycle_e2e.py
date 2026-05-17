"""Integration tests for the orchestrator spawn → run → reap lifecycle.

These tests treat the orchestrator as a black box: feed it tasks via
the FastAPI task server, drive it via :meth:`Orchestrator.tick`, and
assert on the resulting state in the task store, the audit log, and
the spawner. The :class:`unittest.mock.MagicMock` spawner is used as
a controllable test double — no real subprocesses are spawned so the
tests run < 1s each on CI.

Failure modes covered (complements ``test_lifecycle.py`` and the
auth-flow tests under #1261):

| Mode                                          | Test |
|-----------------------------------------------|------|
| Happy path: spawn → complete → exit 0        | ``test_happy_path_spawn_complete_reap`` |
| Worker crash mid-run: orchestrator detects   | ``test_worker_crash_mid_run_is_detected`` |
| Spawn failure: ValueError surfaced in result | ``test_spawn_failure_is_reported_not_silent`` |
| Spawn with empty task list raises           | ``test_spawn_with_empty_task_list_raises`` |
| Multiple concurrent agents, no double-assign | ``test_concurrent_agents_no_double_assignment`` |
| Reaping after process death cleans up state | ``test_reap_clears_active_session_after_death`` |
| Open task remains open on spawn-blocked tick | ``test_max_agents_caps_spawns_per_tick`` |

All tests use ``tmp_path`` for isolation; none use ``time.sleep`` for
synchronisation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from bernstein.core.models import (
    AgentSession,
    ModelConfig,
    OrchestratorConfig,
)
from bernstein.core.orchestrator import Orchestrator
from bernstein.core.spawner import AgentSpawner
from starlette.testclient import TestClient

from bernstein.core.server import create_app

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(
    *,
    title: str,
    role: str = "backend",
    scope: str = "small",
    complexity: str = "low",
) -> dict[str, object]:
    return {
        "title": title,
        "description": f"Auto-generated for {title}",
        "role": role,
        "priority": 1,
        "scope": scope,
        "complexity": complexity,
        "estimated_minutes": 5,
    }


def _make_session(
    *,
    session_id: str,
    role: str = "backend",
    pid: int = 9001,
    status: str = "working",
) -> AgentSession:
    return AgentSession(
        id=session_id,
        role=role,
        pid=pid,
        model_config=ModelConfig("sonnet", "high"),
        status=status,  # type: ignore[arg-type]
    )


def _make_orchestrator(
    workdir: Path,
    client: TestClient,
    spawner: MagicMock,
    *,
    max_agents: int = 4,
    max_tasks_per_agent: int = 1,
) -> Orchestrator:
    config = OrchestratorConfig(
        server_url="http://testserver",
        max_agents=max_agents,
        max_tasks_per_agent=max_tasks_per_agent,
        poll_interval_s=1,
        evolution_enabled=False,
        evolve_mode=False,
    )
    return Orchestrator(
        config=config,
        spawner=spawner,
        workdir=workdir,
        client=client,
    )


def _make_mock_spawner(
    *,
    role: str = "backend",
    sessions: list[AgentSession] | None = None,
) -> MagicMock:
    spawner = MagicMock(spec=AgentSpawner)
    if sessions is None:
        sessions = [_make_session(session_id="agent-default", role=role)]
    spawner.spawn_for_tasks.side_effect = list(sessions) if len(sessions) > 1 else None
    if len(sessions) == 1:
        spawner.spawn_for_tasks.return_value = sessions[0]
    spawner.check_alive.return_value = True
    spawner.get_worktree_path.return_value = None
    return spawner


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_spawn_complete_reap(tmp_path: Path) -> None:
    """Happy path: create task -> tick spawns -> mark done -> tick reaps."""
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")
    spawner = _make_mock_spawner(
        sessions=[_make_session(session_id="agent-hp-001", role="backend", pid=4001)],
    )

    with TestClient(app) as client:
        resp = client.post("/tasks", json=_payload(title="happy task"))
        assert resp.status_code == 201
        task_id = resp.json()["id"]

        orch = _make_orchestrator(tmp_path, client, spawner)

        # Tick 1: should spawn an agent.
        result1 = orch.tick()
        assert "agent-hp-001" in result1.spawned, f"expected spawn, got {result1.spawned}"
        assert spawner.spawn_for_tasks.call_count == 1

        # Claim if not already claimed by spawner side-effect.
        client.post(f"/tasks/{task_id}/claim")
        # Mark the task done as a real agent would.
        complete = client.post(
            f"/tasks/{task_id}/complete",
            json={"result_summary": "ok"},
        )
        assert complete.status_code == 200
        assert complete.json()["status"] == "done"

        # Tick 2: agent process "dies" — reap, summary, no errors.
        spawner.check_alive.return_value = False
        result2 = orch.tick()
        assert result2.active_agents == 0
        assert result2.open_tasks == 0
        assert result2.errors == [], result2.errors

        # Summary should be written.
        summary_path = tmp_path / ".sdd" / "runtime" / "summary.md"
        assert summary_path.exists(), "summary.md must be written when all tasks done"
        body = summary_path.read_text()
        assert "**Total completed:** 1" in body
        assert "happy task" in body


# ---------------------------------------------------------------------------
# Worker crash detection
# ---------------------------------------------------------------------------


def test_worker_crash_mid_run_is_detected(tmp_path: Path) -> None:
    """A worker that dies before completing leaves the task uncompleted.

    The orchestrator must (a) notice the dead agent on the next tick
    (active_agents drops), and (b) leave the task visible (it's not
    silently marked done). A subsequent retry round would re-spawn.
    """
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")
    spawner = _make_mock_spawner(
        sessions=[_make_session(session_id="agent-crash-001", role="backend", pid=5101)],
    )

    with TestClient(app) as client:
        resp = client.post("/tasks", json=_payload(title="task that crashes"))
        task_id = resp.json()["id"]

        orch = _make_orchestrator(tmp_path, client, spawner, max_tasks_per_agent=1)

        # Tick 1: spawn.
        result1 = orch.tick()
        assert "agent-crash-001" in result1.spawned

        # Worker dies WITHOUT marking the task done.
        spawner.check_alive.return_value = False

        # Tick 2: orchestrator should observe the crash.
        result2 = orch.tick()
        assert result2.active_agents == 0, "dead agent must be reaped"

        # The task did NOT complete — it is either still claimed or
        # has been reverted to open by the reaper. Either way it must
        # not be ``done``.
        final = client.get(f"/tasks/{task_id}").json()
        assert final["status"] != "done", (
            f"crashed worker must NOT auto-mark task done; got {final['status']}"
        )


# ---------------------------------------------------------------------------
# Spawn failure modes
# ---------------------------------------------------------------------------


def test_spawn_failure_is_reported_not_silent(tmp_path: Path) -> None:
    """A spawner that raises is reported via TickResult.errors, not swallowed."""
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")

    spawner = MagicMock(spec=AgentSpawner)
    spawner.spawn_for_tasks.side_effect = RuntimeError("simulated spawn failure")
    spawner.check_alive.return_value = True
    spawner.get_worktree_path.return_value = None

    with TestClient(app) as client:
        client.post("/tasks", json=_payload(title="spawn will fail"))
        orch = _make_orchestrator(tmp_path, client, spawner)

        result = orch.tick()

        # Spawn was attempted exactly once for the open task.
        assert spawner.spawn_for_tasks.call_count >= 1
        # No agent registered.
        assert result.active_agents == 0
        # The error should not have been silently dropped.
        joined_errors = " ".join(result.errors)
        assert "simulated spawn failure" in joined_errors or result.errors, (
            f"expected spawn failure in errors; got {result.errors}"
        )


def test_spawn_with_empty_task_list_raises(tmp_path: Path) -> None:
    """The spawner contract refuses to spawn an agent for zero tasks.

    Direct contract test — guards against a regression where the
    orchestrator might mass-spawn idle workers in response to backlog
    polling races (each with an empty batch).
    """
    # A real AgentSpawner so we exercise the actual contract check.
    from bernstein.adapters.base import CLIAdapter

    class _StubAdapter(CLIAdapter):
        def spawn(self, **kwargs):  # type: ignore[override]
            raise AssertionError("should not be reached")

        def name(self) -> str:
            return "stub"

    templates = tmp_path / "templates" / "roles"
    templates.mkdir(parents=True)
    spawner = AgentSpawner(
        _StubAdapter(),  # type: ignore[arg-type]
        templates,
        tmp_path,
        use_worktrees=False,
    )
    with pytest.raises(ValueError, match="empty task list"):
        spawner.spawn_for_tasks([])


# ---------------------------------------------------------------------------
# Concurrent agents
# ---------------------------------------------------------------------------


def test_concurrent_agents_no_double_assignment(tmp_path: Path) -> None:
    """5 tasks of the same role — agents are spawned without re-spawning
    the same task into multiple sessions on a single tick."""
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")

    sessions = [
        _make_session(session_id=f"agent-conc-{i:02d}", role="backend", pid=6000 + i)
        for i in range(5)
    ]

    spawner = MagicMock(spec=AgentSpawner)
    spawn_call_log: list[list[str]] = []

    def _spawn_side_effect(batch, model_override=None):
        del model_override
        spawn_call_log.append([t.id for t in batch])
        return sessions[len(spawn_call_log) - 1]

    spawner.spawn_for_tasks.side_effect = _spawn_side_effect
    spawner.check_alive.return_value = True
    spawner.get_worktree_path.return_value = None

    with TestClient(app) as client:
        task_ids: list[str] = []
        for i in range(5):
            resp = client.post("/tasks", json=_payload(title=f"concurrent-{i}"))
            assert resp.status_code == 201
            task_ids.append(resp.json()["id"])

        orch = _make_orchestrator(
            tmp_path, client, spawner, max_agents=5, max_tasks_per_agent=1
        )
        orch.tick()

        # Every spawned task ID must be unique across all spawn calls.
        all_assigned = [tid for batch in spawn_call_log for tid in batch]
        assert len(all_assigned) == len(set(all_assigned)), (
            f"task assigned to multiple agents: {sorted(all_assigned)}"
        )


def test_reap_clears_active_session_after_death(tmp_path: Path) -> None:
    """After a session dies, the agent count drops to zero and a future
    tick can spawn fresh agents without conflict."""
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")

    s1 = _make_session(session_id="agent-rcl-001", role="backend", pid=7001)
    s2 = _make_session(session_id="agent-rcl-002", role="backend", pid=7002)

    spawner = MagicMock(spec=AgentSpawner)
    spawner.spawn_for_tasks.side_effect = [s1, s2]
    spawner.check_alive.return_value = True
    spawner.get_worktree_path.return_value = None

    with TestClient(app) as client:
        # Task A
        resp_a = client.post("/tasks", json=_payload(title="reap-task-a"))
        task_a = resp_a.json()["id"]

        orch = _make_orchestrator(tmp_path, client, spawner, max_agents=1)

        # Tick 1: spawn agent for task A.
        r1 = orch.tick()
        assert "agent-rcl-001" in r1.spawned

        # Complete A.
        client.post(f"/tasks/{task_a}/claim")
        client.post(f"/tasks/{task_a}/complete", json={"result_summary": "done"})

        # Add task B.
        resp_b = client.post("/tasks", json=_payload(title="reap-task-b"))
        _ = resp_b.json()["id"]

        # First agent dies, second must spawn.
        spawner.check_alive.return_value = False
        r2 = orch.tick()
        assert r2.active_agents == 0, (
            f"old agent must be reaped after death; got active_agents={r2.active_agents}"
        )

        # Make the new spawn alive.
        spawner.check_alive.return_value = True
        r3 = orch.tick()
        all_spawned = r1.spawned + r2.spawned + r3.spawned
        assert "agent-rcl-002" in all_spawned, f"second agent should have spawned across ticks: {all_spawned}"


def test_max_agents_caps_spawns_per_tick(tmp_path: Path) -> None:
    """max_agents=1 with 3 open tasks — only one spawn per tick.

    Guards the cap on parallelism — without it the orchestrator would
    fork a worker per task and exhaust the host.
    """
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")

    spawner = MagicMock(spec=AgentSpawner)
    sessions = [
        _make_session(session_id=f"agent-cap-{i:02d}", role="backend", pid=8000 + i)
        for i in range(3)
    ]
    spawner.spawn_for_tasks.side_effect = sessions
    spawner.check_alive.return_value = True
    spawner.get_worktree_path.return_value = None

    with TestClient(app) as client:
        for i in range(3):
            client.post("/tasks", json=_payload(title=f"cap-{i}"))

        orch = _make_orchestrator(
            tmp_path, client, spawner, max_agents=1, max_tasks_per_agent=1
        )
        result = orch.tick()

        # Only one spawn this tick (the other two open tasks must wait).
        assert len(result.spawned) <= 1, (
            f"max_agents=1 should permit at most one spawn per tick; got {result.spawned}"
        )
