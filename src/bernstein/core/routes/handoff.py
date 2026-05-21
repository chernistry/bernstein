"""Dashboard route for session handoff (op-005).

The web dashboard exposes ``GET /handoff/<token>`` so a browser session
can claim a token emitted from the terminal or a chat bridge. The
endpoint:

1. Resolves ``<token>`` against the on-disk
   :class:`~bernstein.core.handoff.HandoffTokenStore`.
2. Marks the token consumed (single-claim semantics).
3. Returns the session/task identity plus the recent stream tail so the
   browser can render the last N lines without waiting for the live
   stream to catch up.

Errors map to HTTP status codes:

* ``404`` - token never issued.
* ``410`` - token expired or already claimed.

The route uses ``request.app.state.workdir`` when set (the test server
sets this); otherwise it falls back to the directory containing the
``.sdd/`` runtime path. Keeping the logic resolver-light here means the
route stays trivial and the heavy lifting lives in the core module.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from bernstein.core.handoff import (
    HandoffClaimError,
    HandoffTokenStore,
    HandoffUnknownTokenError,
    StreamTailBuffer,
)

router = APIRouter()


def _resolve_workdir(request: Request) -> Path:
    """Locate the project root from app state.

    The server sets ``app.state.workdir`` when it knows the project
    root; otherwise we fall back to the parent of
    ``app.state.runtime_dir`` (which lives at ``.sdd/runtime``).
    """
    workdir = getattr(request.app.state, "workdir", None)
    if isinstance(workdir, Path):
        return workdir
    runtime_dir = getattr(request.app.state, "runtime_dir", None)
    if isinstance(runtime_dir, Path):
        # ``.sdd/runtime`` -> project root is two levels up.
        return runtime_dir.parent.parent
    return Path.cwd()


@router.get("/handoff/{token}")
def claim_handoff_token(token: str, request: Request) -> JSONResponse:
    """Claim a handoff token and return the session identity + tail.

    Args:
        token: Opaque urlsafe token presented by the dashboard.
        request: FastAPI request (used to resolve the workdir).

    Returns:
        JSON envelope with ``session_id``, ``task_id``,
        ``source_surface``, ``claimed_at``, ``note`` and ``tail`` (a
        list of recent stream entries).

    Raises:
        HTTPException: ``404`` for unknown tokens, ``410`` for expired
        or already-claimed tokens.
    """
    workdir = _resolve_workdir(request)
    store = HandoffTokenStore(workdir)
    try:
        record = store.claim(token, claimed_by="dashboard")
    except HandoffUnknownTokenError as exc:
        raise HTTPException(status_code=404, detail="unknown handoff token") from exc
    except HandoffClaimError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc

    tail_limit = _tail_limit(request)
    tail_entries = StreamTailBuffer(workdir, record.session_id).read(limit=tail_limit)

    payload: dict[str, object] = {
        "session_id": record.session_id,
        "task_id": record.task_id,
        "source_surface": record.source_surface,
        "claimed_at": record.claimed_at,
        "note": record.note,
        "tail": [entry.to_dict() for entry in tail_entries],
    }
    return JSONResponse(payload)


def _tail_limit(request: Request) -> int:
    """Resolve the requested tail size from the ``tail`` query string.

    Defaults to 50 lines and is hard-capped at 500 to mirror the
    on-disk ring buffer cap.
    """
    raw = request.query_params.get("tail")
    if raw is None:
        return 50
    try:
        value = int(raw)
    except ValueError:
        return 50
    return max(0, min(value, 500))


__all__ = ["router"]


# Helpful for tests that build a minimal app: re-export the workdir resolver.
def resolve_workdir_for_request(request: Request) -> Path:
    """Public alias of ``_resolve_workdir`` for test composition."""
    return _resolve_workdir(cast("Request", request))
