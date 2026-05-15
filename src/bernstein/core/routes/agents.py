"""Agent inspection routes — logs, kill signals, and SSE output streams."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from bernstein.core.server import AgentKillResponse, AgentLogsResponse, read_log_tail

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

router = APIRouter()

# Maximum idle poll ticks before the SSE stream closes (prevents infinite hang in tests).
_MAX_IDLE_TICKS = 30
_POLL_INTERVAL = 1.0


def _runtime_dir(request: Request) -> Path:
    return request.app.state.runtime_dir  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# GET /agents — list of all known agent sessions (web GUI list view)
# ---------------------------------------------------------------------------


def _task_store(request: Request) -> Any:
    return request.app.state.store


@router.get("/agents")
def list_agents(request: Request) -> list[dict[str, Any]]:
    """Return a flat list of agent sessions for the web GUI grid.

    Serializes ``TaskStore.agents()`` into a JSON-friendly shape that
    matches the Agents screen's expectations (status, role, duration,
    tokens, current task title).
    """
    import time as _time

    store = _task_store(request)
    # ``agents`` is a @property on TaskStore returning dict[str, AgentSession].
    sessions = getattr(store, "agents", {}) or {}

    now = _time.time()
    out: list[dict[str, Any]] = []
    for sid, s in sessions.items():
        spawn_ts = getattr(s, "spawn_ts", 0.0) or 0.0
        duration_ms = max(0, int((now - spawn_ts) * 1000)) if spawn_ts else None
        task_ids = list(getattr(s, "task_ids", []) or [])
        current_task_title: str | None = None
        if task_ids and hasattr(store, "get_task"):
            try:
                t = store.get_task(task_ids[0])
                if t is not None:
                    current_task_title = getattr(t, "title", None)
            except Exception:
                current_task_title = None
        out.append(
            {
                "id": sid,
                "session_id": sid,
                "role": getattr(s, "role", ""),
                "status": getattr(s, "status", "starting"),
                "spawn_ts": spawn_ts,
                "heartbeat_ts": getattr(s, "heartbeat_ts", 0.0),
                "duration_ms": duration_ms,
                "tokens_used": getattr(s, "tokens_used", 0),
                "tokens_in": getattr(s, "tokens_used", 0),
                "tokens_out": 0,
                "context_utilization_pct": getattr(s, "context_utilization_pct", 0.0),
                "task_ids": task_ids,
                "current_task_id": task_ids[0] if task_ids else None,
                "current_task_title": current_task_title,
                "model": getattr(getattr(s, "model_config", None), "name", None),
                "provider": getattr(s, "provider", None),
                "cost_usd": 0.0,
                "exit_code": getattr(s, "exit_code", None),
            }
        )
    return out


# ---------------------------------------------------------------------------
# GET /agents/comparison — placeholder for compare-two view (Phase 2)
# ---------------------------------------------------------------------------


@router.get("/agents/comparison")
def agents_comparison(request: Request) -> dict[str, Any]:
    """Placeholder comparison endpoint — returns the same shape as `/agents`."""
    return {"agents": list_agents(request)}


# ---------------------------------------------------------------------------
# GET /agents/{session_id}/logs
# ---------------------------------------------------------------------------


@router.get(
    "/agents/{session_id}/logs",
    responses={404: {"description": "No log for session"}},
)
def agent_logs(
    request: Request,
    session_id: str,
    tail_bytes: int = 0,
) -> AgentLogsResponse:
    """Return the log content for a running or finished agent session.

    Args:
        session_id: Agent session identifier (e.g. ``backend-abc12345``).
        tail_bytes: When > 0, only the last *tail_bytes* of the file are
            returned (partial leading line is stripped).  0 means full file.

    Returns:
        JSON with ``session_id``, ``content`` string, and ``size`` in bytes.

    Raises:
        HTTPException: 404 when no log file exists for the session.
    """
    runtime_dir = _runtime_dir(request)
    log_path = runtime_dir / f"{session_id}.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"No log for session {session_id!r}")

    size = log_path.stat().st_size
    offset = max(0, size - tail_bytes) if tail_bytes > 0 else 0
    content = read_log_tail(log_path, offset)

    return AgentLogsResponse(session_id=session_id, content=content, size=size)


# ---------------------------------------------------------------------------
# POST /agents/{session_id}/kill
# ---------------------------------------------------------------------------


@router.post("/agents/{session_id}/kill")
def agent_kill(request: Request, session_id: str) -> AgentKillResponse:
    """Request termination of an agent by writing a ``.kill`` signal file.

    The orchestrator polls for these files on each tick and calls
    ``spawner.kill(session)`` for the matching session.

    Args:
        session_id: Agent session to terminate.

    Returns:
        JSON with ``session_id`` and ``kill_requested: true``.
    """
    runtime_dir = _runtime_dir(request)
    kill_file = runtime_dir / f"{session_id}.kill"
    kill_file.write_text(str(time.time()), encoding="utf-8")
    return AgentKillResponse(session_id=session_id, kill_requested=True)


# ---------------------------------------------------------------------------
# GET /agents/{session_id}/stream  (SSE)
# ---------------------------------------------------------------------------


@router.get("/agents/{session_id}/stream")
def agent_stream(request: Request, session_id: str) -> StreamingResponse:
    """Server-Sent Events stream of agent output for the given session.

    Replays all existing log content first, then tails the file for new
    lines.  Closes automatically after ``_MAX_IDLE_TICKS`` consecutive
    polls with no new data (or no log file).

    SSE event format::

        data: {"type": "connected", "session_id": "<id>"}

        data: {"line": "<log line>"}

    Args:
        session_id: Agent session to stream output for.

    Returns:
        StreamingResponse with ``text/event-stream`` media type.
    """
    runtime_dir = _runtime_dir(request)
    log_path = runtime_dir / f"{session_id}.log"

    async def _generate() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id})}\n\n"

        offset = 0
        idle_ticks = 0

        while idle_ticks < _MAX_IDLE_TICKS:
            if await request.is_disconnected():
                break

            if not log_path.exists():
                await asyncio.sleep(_POLL_INTERVAL)
                idle_ticks += 1
                continue

            new_size = log_path.stat().st_size
            content = read_log_tail(log_path, offset)

            if not content:
                idle_ticks += 1
                await asyncio.sleep(_POLL_INTERVAL)
                continue

            for line in content.splitlines():
                if line:
                    yield f"data: {json.dumps({'line': line})}\n\n"
            offset = new_size
            idle_ticks = 0

            await asyncio.sleep(_POLL_INTERVAL)

    return StreamingResponse(_generate(), media_type="text/event-stream")
