"""Spawner glue that routes adapter exec through a :class:`SandboxSession`.

Phase 2 of ``oai-002`` (ticket ``oai-002b``). Phase 1 shipped the
:class:`~bernstein.core.sandbox.backend.SandboxBackend` protocol and
the optional ``sandbox_session`` parameter on :class:`AgentSpawner`,
but adapter subprocess launches still went straight to the host
worktree. This module owns the *routing seam* — the bit that, when a
session is attached, performs:

1. Prompt injection via :meth:`SandboxSession.write` so the agent
   command can read the prompt file from inside whatever workspace
   the backend has provisioned.
2. Adapter command execution via :meth:`SandboxSession.exec` running on
   a dedicated background thread (the spawner is sync; the session
   protocol is async).
3. Liveness tracking so :meth:`AgentSpawner.check_alive` and
   :meth:`AgentSpawner.kill` keep working without subprocess PIDs.

The worktree-direct path is preserved unchanged when no session is
attached — existing users see byte-identical behaviour. The 35 adapters
themselves are not refactored in this phase; they continue to expose
:meth:`CLIAdapter.spawn`, which we still call as a fallback when the
selected sandbox is the local-worktree backend (``backend_name ==
"worktree"``) so the worker-wrapper / process-group bookkeeping that
production tooling relies on stays intact. Cloud / container backends
take the new ``session.exec`` path.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bernstein.core.sandbox.backend import ExecResult, SandboxSession

logger = logging.getLogger(__name__)


@dataclass
class SandboxExecHandle:
    """Bookkeeping for an adapter command running inside a session.

    Attributes:
        session_id: Owning agent session identifier.
        backend_name: Name of the sandbox backend running the command
            (used for label cardinality on Prometheus metrics).
        future: :class:`~concurrent.futures.Future` resolving to the
            :class:`~bernstein.core.sandbox.backend.ExecResult` once the
            command exits. The orchestrator polls
            :meth:`Future.done` for liveness.
        log_path: Local path where the command's stdout/stderr is
            mirrored once the future resolves. Pre-allocated so other
            modules can wire it as the agent log path before the
            command finishes.
        loop: Event loop running the underlying ``session.exec`` task.
            Owned by :func:`_run_session_loop`; used to schedule
            cancellation requests via :meth:`AgentSpawner.kill`.
        task: The asyncio task wrapping the ``session.exec`` call.
            ``None`` until the loop has scheduled it; consult
            :attr:`future` for the canonical lifecycle signal.
        started_at: ``time.monotonic()`` snapshot when scheduling
            began. Used to surface duration metrics on completion.
    """

    session_id: str
    backend_name: str
    future: Future[ExecResult]
    log_path: Path
    loop: asyncio.AbstractEventLoop
    task: asyncio.Task[ExecResult] | None = None
    started_at: float = field(default_factory=time.monotonic)


def _run_session_loop(
    *,
    coro_factory: Callable[[], object],
    handle: SandboxExecHandle,
) -> None:
    """Run the per-handle event loop on its dedicated thread.

    The spawner is synchronous; :meth:`SandboxSession.exec` is async.
    Spinning up one tiny event loop per session keeps the seam local —
    we don't need a global ``asyncio.run`` in the hot spawn path, and
    cancellation stays scoped to the agent that asked for it.

    Args:
        coro_factory: Zero-arg callable that returns the awaitable to
            run (we accept a factory rather than the coroutine itself
            so the loop owns coroutine creation and Pyright stops
            warning about cross-thread coroutine reuse).
        handle: Bookkeeping record updated with the running task once
            scheduled.
    """
    loop = handle.loop
    asyncio.set_event_loop(loop)
    try:
        coro = coro_factory()
        task: asyncio.Task[ExecResult] = loop.create_task(coro)  # type: ignore[arg-type]
        handle.task = task
        try:
            result = loop.run_until_complete(task)
        except asyncio.CancelledError:
            handle.future.cancel()
            return
        except BaseException as exc:
            handle.future.set_exception(exc)
            return
        handle.future.set_result(result)
    finally:
        try:
            loop.close()
        except Exception:  # pragma: no cover — defensive
            logger.debug("loop close raised", exc_info=True)


def submit_session_exec(
    *,
    session: SandboxSession,
    cmd: list[str],
    session_id: str,
    log_path: Path,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> SandboxExecHandle:
    """Run *cmd* through ``session.exec`` on a dedicated background thread.

    The returned :class:`SandboxExecHandle` carries the future that
    resolves to the :class:`~bernstein.core.sandbox.backend.ExecResult`
    plus enough bookkeeping for liveness checks and cancellation.

    Args:
        session: The :class:`SandboxSession` produced by
            :meth:`SandboxBackend.create`.
        cmd: Argv list. Must be non-empty. Backends do not pipe through
            a shell — wrap commands manually when shell semantics are
            needed.
        session_id: Owning agent session ID used for log/error context.
        log_path: Path the spawner has reserved for this agent's log.
            Mirrored stdout/stderr is written here once the command
            completes (best effort — failures are logged at debug
            level).
        cwd: Optional working directory inside the sandbox. ``None``
            uses :attr:`SandboxSession.workdir`.
        env: Optional extra environment merged on top of the backend's
            base environment.
        timeout: Wall-clock timeout in seconds; ``None`` lets the
            backend pick its default.

    Returns:
        A :class:`SandboxExecHandle` whose :attr:`SandboxExecHandle.future`
        resolves once the command exits.
    """
    if not cmd:
        raise ValueError("cmd must be a non-empty argv list")

    fut: Future[ExecResult] = Future()
    loop = asyncio.new_event_loop()
    handle = SandboxExecHandle(
        session_id=session_id,
        backend_name=getattr(session, "backend_name", "unknown"),
        future=fut,
        log_path=log_path,
        loop=loop,
    )

    def _factory() -> object:
        return session.exec(cmd, cwd=cwd, env=env, timeout=timeout)

    thread = threading.Thread(
        target=_run_session_loop,
        kwargs={"coro_factory": _factory, "handle": handle},
        name=f"sandbox-exec-{session_id}",
        daemon=True,
    )
    thread.start()

    # Mirror logs once the future resolves so consumers can tail
    # log_path even though session.exec captures bytes in memory.
    def _persist_log(f: Future[ExecResult]) -> None:
        if f.cancelled() or f.exception() is not None:
            return
        result = f.result()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as fh:
                fh.write(result.stdout)
                if result.stderr:
                    fh.write(b"\n--- stderr ---\n")
                    fh.write(result.stderr)
        except OSError as exc:  # pragma: no cover — best effort
            logger.debug("Could not mirror sandbox exec log to %s: %s", log_path, exc)

    fut.add_done_callback(_persist_log)
    return handle


def cancel_session_exec(handle: SandboxExecHandle) -> None:
    """Best-effort cancellation of a running sandbox exec.

    Schedules cancellation of the underlying asyncio task on the
    handle's owning loop. Idempotent — safe to call after the future
    already resolved.

    Args:
        handle: The handle returned by :func:`submit_session_exec`.
    """
    if handle.future.done():
        return
    task = handle.task
    if task is None:
        # Loop hasn't scheduled the task yet; mark the future cancelled.
        handle.future.cancel()
        return
    handle.loop.call_soon_threadsafe(task.cancel)


def write_prompt_to_session(
    *,
    session: SandboxSession,
    prompt: str,
    session_id: str,
) -> str:
    """Persist *prompt* to the sandbox via :meth:`SandboxSession.write`.

    Returns the path the adapter command should read inside the
    sandbox. Centralised so the spawner and any future plug-in points
    use the same convention as :func:`AgentSpawner._spawn_in_container`
    (``.sdd/runtime/prompts/<session_id>.md`` relative to ``workdir``).

    Args:
        session: The active sandbox session.
        prompt: The rendered agent prompt.
        session_id: Agent session ID, used as the file basename.

    Returns:
        The relative path inside the sandbox where the prompt was
        written. Adapters can read it via ``$WORKDIR/<rel_path>`` or
        treat it as relative to :attr:`SandboxSession.workdir`.
    """
    rel_path = f".sdd/runtime/prompts/{session_id}.md"
    payload = prompt.encode("utf-8")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(session.write(rel_path, payload))
    finally:
        loop.close()
    return rel_path


__all__ = [
    "SandboxExecHandle",
    "cancel_session_exec",
    "submit_session_exec",
    "write_prompt_to_session",
]
