"""Unit tests for AgentSpawner sandbox-session routing (oai-002 phase 2)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from bernstein.core.models import AgentSession, ModelConfig
from bernstein.core.spawner import AgentSpawner

from bernstein.adapters.base import CLIAdapter, SpawnResult
from bernstein.core.sandbox.backend import ExecResult, SandboxSession

if TYPE_CHECKING:
    from collections.abc import Mapping


class _FakeAdapter(CLIAdapter):
    """Minimal adapter that records direct-spawn calls."""

    def __init__(self, adapter_name: str = "claude") -> None:
        super().__init__()
        self._name = adapter_name
        self.spawn_calls: list[tuple[str, Path]] = []

    def name(self) -> str:
        return self._name

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, object] | None = None,
        timeout_seconds: int = 1800,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> SpawnResult:
        del model_config, session_id, mcp_config, timeout_seconds
        del task_scope, budget_multiplier, system_addendum
        self.spawn_calls.append((prompt, workdir))
        return SpawnResult(pid=99, log_path=workdir / ".sdd" / "logs" / "direct.log")

    def is_alive(self, pid: int) -> bool:  # pragma: no cover - not used
        return pid == 99

    def kill(self, pid: int) -> None:  # pragma: no cover - not used
        del pid


class _FakeSession(SandboxSession):
    """In-memory :class:`SandboxSession` for spawner-routing tests."""

    def __init__(self, *, backend_name: str, root: Path) -> None:
        self.backend_name = backend_name
        self.session_id = "fake-sess"
        self.workdir = str(root)
        self._root = root
        self._exec_calls: list[list[str]] = []
        self._exec_blocker: asyncio.Event | None = None
        self.exit_code = 0

    @property
    def exec_calls(self) -> list[list[str]]:
        return self._exec_calls

    async def read(self, path: str) -> bytes:
        return (self._root / path).read_bytes()

    async def write(self, path: str, data: bytes, *, mode: int = 0o644) -> None:
        del mode
        target = self._root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: bytes | None = None,
    ) -> ExecResult:
        del cwd, env, timeout, stdin
        self._exec_calls.append(list(cmd))
        if self._exec_blocker is not None:
            await self._exec_blocker.wait()
        return ExecResult(
            exit_code=self.exit_code,
            stdout=b"hello",
            stderr=b"",
            duration_seconds=0.01,
        )

    async def ls(self, path: str) -> list[str]:
        target = self._root / path
        return sorted(p.name for p in target.iterdir())

    async def snapshot(self) -> str:
        raise NotImplementedError

    async def shutdown(self) -> None:
        pass


def _build_spawner(tmp_path: Path, *, session: SandboxSession | None) -> tuple[AgentSpawner, _FakeAdapter]:
    adapter = _FakeAdapter("claude")
    with patch("bernstein.core.agents.spawner_core.get_registry", return_value=MagicMock()):
        spawner = AgentSpawner(
            adapter=adapter,
            templates_dir=tmp_path,
            workdir=tmp_path,
            use_worktrees=False,
            sandbox_session=session,
        )
    return spawner, adapter


def test_spawn_via_sandbox_session_routes_through_session(tmp_path: Path) -> None:
    """A non-worktree session causes exec/file ops to run via the session."""
    session_obj = _FakeSession(backend_name="docker", root=tmp_path)
    spawner, adapter = _build_spawner(tmp_path, session=session_obj)
    agent_session = AgentSession(id="S-7", role="backend")

    result = spawner._spawn_via_sandbox_session(  # pyright: ignore[reportPrivateUsage]
        session_id="S-7",
        prompt="solve it",
        spawn_cwd=tmp_path,
        model_config=ModelConfig("sonnet", "high"),
        mcp_config=None,
        session=agent_session,
        adapter=adapter,
    )

    # Wait for the background thread's future to resolve.
    handle = spawner._sandbox_exec_handles["S-7"]  # pyright: ignore[reportPrivateUsage]
    handle.future.result(timeout=5.0)

    assert result.pid == 0
    assert agent_session.isolation == "container"
    assert agent_session.runtime_backend == "docker"
    assert adapter.spawn_calls == []
    # Prompt was written to the session-managed workdir.
    prompt_path = tmp_path / ".sdd" / "runtime" / "prompts" / "S-7.md"
    assert prompt_path.read_bytes() == b"solve it"
    # Adapter command was executed via session.exec at least once.
    assert session_obj.exec_calls, "session.exec was never invoked"


def test_worktree_session_does_not_trigger_routing(tmp_path: Path) -> None:
    """Worktree-backed sessions intentionally stay on the legacy path."""
    session_obj = _FakeSession(backend_name="worktree", root=tmp_path)
    spawner, _ = _build_spawner(tmp_path, session=session_obj)

    # Re-implement the dispatcher's gate inline so the test asserts the
    # exact predicate used in :meth:`AgentSpawner.spawn_for_tasks`.
    routes_through_session = (
        spawner.sandbox_session is not None
        and getattr(spawner.sandbox_session, "backend_name", "worktree") != "worktree"
    )
    assert routes_through_session is False
    # The session is still exposed for visibility, just not used for exec.
    assert spawner.sandbox_session is session_obj


def test_sandbox_session_check_alive_and_kill(tmp_path: Path) -> None:
    """Liveness and kill paths cooperate with the in-flight session future."""
    session_obj = _FakeSession(backend_name="docker", root=tmp_path)
    # Block exec until kill triggers cancellation, so the "still alive"
    # branch of _check_alive_sandbox_session is observable.
    block_loop = asyncio.new_event_loop()
    try:
        session_obj._exec_blocker = asyncio.Event()  # pyright: ignore[reportPrivateUsage]
    finally:
        block_loop.close()

    spawner, _ = _build_spawner(tmp_path, session=session_obj)
    agent_session = AgentSession(id="S-9", role="backend")

    spawner._spawn_via_sandbox_session(  # pyright: ignore[reportPrivateUsage]
        session_id="S-9",
        prompt="busy",
        spawn_cwd=tmp_path,
        model_config=ModelConfig("sonnet", "high"),
        mcp_config=None,
        session=agent_session,
        adapter=_FakeAdapter("claude"),
    )

    # Liveness should report True while the future has not resolved.
    assert spawner._check_alive_sandbox_session(agent_session) is True  # pyright: ignore[reportPrivateUsage]

    # Kill cancels the future and clears the handle.
    spawner._kill_local(agent_session)  # pyright: ignore[reportPrivateUsage]
    assert "S-9" not in spawner._sandbox_exec_handles  # pyright: ignore[reportPrivateUsage]
    assert agent_session.status == "dead"
