"""Live terminal-peek route for the dashboard (#1217 — slice 1).

Operators want to glance at a running agent's recent stdout/stderr from a
browser without having to ``bernstein attach`` over SSH. This module
delivers the smallest viable slice of that feature:

* ``GET /sessions/{session_id}/peek`` — returns the last N entries of the
  session's stream-tail ring buffer as JSON.  Polled by the static page
  every few seconds.
* ``GET /dashboard/peek/{session_id}`` — vanilla-JS HTML page that polls
  the JSON endpoint and renders the lines.
* ``GET /dashboard/peek`` — 2x2 tile grid that watches up to four
  sessions side-by-side; session ids come in via the
  ``s1`` / ``s2`` / ``s3`` / ``s4`` query parameters.

Deferred (tracked on #1217 for follow-up slices):

* WebSocket streaming — the polling loop is enough at <500ms latency on
  loopback for the dashboard tile.
* Send-bar (write back into the agent's stdin).
* Tunnel-publish auth so the page is safe to expose beyond loopback.

The buffer source is the same ``StreamTailBuffer`` the handoff route
reads, so the JSON shape matches ``GET /handoff/{token}#tail`` and the
page can reuse the rendering logic from the handoff page when the next
slice lands.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Final

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bernstein.core.handoff import StreamTailBuffer

router = APIRouter()

# Hard caps mirror the on-disk ring buffer cap so callers can't DoS by
# asking for absurd tail sizes. ``DEFAULT_TAIL`` matches the issue's
# "default 200 lines" guidance.
DEFAULT_TAIL: Final[int] = 200
MAX_TAIL: Final[int] = 500
# Session ids in Bernstein are slug-shaped (``sess-xxx``, ``t-yyy``).
# Reject anything containing path separators or HTML-active characters
# before we hand the value to the buffer or render it into the page.
_SESSION_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


def _resolve_workdir(request: Request) -> Path:
    """Locate the project root from app state.

    Mirrors the resolver used by the handoff route so both endpoints see
    the same ``.sdd/`` runtime layout. The server attaches
    ``app.state.workdir`` at startup; the fallback path keeps the unit
    tests' minimal ``FastAPI()`` shells working.
    """
    workdir = getattr(request.app.state, "workdir", None)
    if isinstance(workdir, Path):
        return workdir
    runtime_dir = getattr(request.app.state, "runtime_dir", None)
    if isinstance(runtime_dir, Path):
        return runtime_dir.parent.parent
    return Path.cwd()


def _tail_limit(request: Request) -> int:
    """Resolve the ``tail`` query argument, clamped to ``[0, MAX_TAIL]``."""
    raw = request.query_params.get("tail")
    if raw is None:
        return DEFAULT_TAIL
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TAIL
    return max(0, min(value, MAX_TAIL))


def _validate_session_id(session_id: str) -> str:
    """Reject session ids that aren't safe to embed in HTML or paths.

    Args:
        session_id: User-supplied session identifier.

    Returns:
        The same id when it passes the slug check.

    Raises:
        HTTPException: ``400`` when the id contains path separators or
            other characters that could escape the JSONL buffer path or
            inject HTML when echoed back into the page.
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="invalid session id")
    return session_id


@router.get("/sessions/{session_id}/peek")
def peek_session(session_id: str, request: Request) -> JSONResponse:
    """Return the recent stream-tail entries for ``session_id``.

    Args:
        session_id: Bernstein session whose tail to read.
        request: FastAPI request — used to resolve the workdir and the
            ``tail`` query argument.

    Returns:
        JSON envelope with ``session_id`` plus a ``tail`` list of
        ``{ts, surface, text}`` entries in chronological order. An
        empty list signals "buffer not initialised yet" rather than an
        error so the polling page renders a blank pane while it waits.
    """
    safe_id = _validate_session_id(session_id)
    workdir = _resolve_workdir(request)
    limit = _tail_limit(request)
    entries = StreamTailBuffer(workdir, safe_id).read(limit=limit)
    return JSONResponse(
        {
            "session_id": safe_id,
            "tail": [entry.to_dict() for entry in entries],
        }
    )


@router.get("/dashboard/peek/{session_id}", response_class=HTMLResponse, include_in_schema=False)
def peek_page(session_id: str) -> HTMLResponse:
    """Single-session live-peek page; polls the JSON endpoint."""
    safe_id = html.escape(_validate_session_id(session_id))
    return HTMLResponse(_render_single_page(safe_id))


@router.get("/dashboard/peek", response_class=HTMLResponse, include_in_schema=False)
def peek_grid(request: Request) -> HTMLResponse:
    """2x2 tile grid for up to four sessions.

    Reads up to four ``s1`` ... ``s4`` query parameters; missing slots
    render as placeholder tiles so the layout stays stable on a phone
    viewport. Each id is validated and HTML-escaped before injection.
    """
    raw_ids = [request.query_params.get(f"s{i}") for i in (1, 2, 3, 4)]
    safe_ids: list[str | None] = []
    for raw in raw_ids:
        if raw is None or not raw.strip():
            safe_ids.append(None)
            continue
        if not _SESSION_ID_RE.fullmatch(raw):
            raise HTTPException(status_code=400, detail="invalid session id")
        safe_ids.append(html.escape(raw))
    return HTMLResponse(_render_grid_page(safe_ids))


# ---------------------------------------------------------------------------
# Inline HTML — vanilla JS, no framework. Two pages share the head + script.
# ---------------------------------------------------------------------------

_PAGE_HEAD = """\
<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>session peek</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; padding: 0; height: 100%; background: #0b0d10; color: #cbd2da;
               font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header { padding: 8px 12px; background: #141a21; border-bottom: 1px solid #232b34;
           display: flex; gap: 8px; align-items: baseline; }
  header b { color: #f1f5f9; }
  header .meta { color: #6b7785; font-size: 11px; margin-left: auto; }
  pre.tail { margin: 0; padding: 8px 12px; white-space: pre-wrap; word-break: break-word;
             height: calc(100% - 36px); overflow-y: auto; }
  .grid { display: grid; gap: 6px; height: 100vh; padding: 6px; box-sizing: border-box;
          grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; background: #05070a; }
  .tile { display: flex; flex-direction: column; min-height: 0; background: #0b0d10;
          border: 1px solid #232b34; border-radius: 4px; overflow: hidden; }
  .tile.empty pre.tail { color: #4b5563; }
  .err { color: #ef4444; }
</style>
"""

_PAGE_SCRIPT = """\
<script>
(function () {
  const POLL_MS = 2000;
  function attach(node) {
    const id = node.dataset.session;
    if (!id) return;
    const out = node.querySelector('pre.tail');
    const meta = node.querySelector('.meta');
    let stopped = false;
    async function tick() {
      try {
        const res = await fetch('/sessions/' + encodeURIComponent(id) + '/peek?tail=200',
                                { credentials: 'same-origin' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const body = await res.json();
        const lines = (body.tail || []).map(e => e.text).join('\\n');
        out.textContent = lines || '(buffer empty)';
        out.scrollTop = out.scrollHeight;
        if (meta) meta.textContent = (body.tail || []).length + ' lines';
      } catch (err) {
        if (meta) meta.textContent = 'error: ' + err.message;
        out.classList.add('err');
      }
      if (!stopped) setTimeout(tick, POLL_MS);
    }
    tick();
  }
  document.querySelectorAll('[data-session]').forEach(attach);
})();
</script>
"""


def _render_single_page(safe_id: str) -> str:
    """Render the single-session peek page. ``safe_id`` is HTML-escaped."""
    return f"""{_PAGE_HEAD}
<header><b>peek</b> {safe_id} <span class="meta">starting...</span></header>
<pre class="tail" data-session="{safe_id}">(loading...)</pre>
{_PAGE_SCRIPT}
"""


def _render_grid_page(safe_ids: list[str | None]) -> str:
    """Render the 2x2 tile page. ``safe_ids`` entries are pre-escaped or None."""
    tiles: list[str] = []
    for idx, sid in enumerate(safe_ids, start=1):
        if sid is None:
            tiles.append(
                f'<div class="tile empty">'
                f"<header><b>tile {idx}</b> "
                f'<span class="meta">add ?s{idx}=&lt;session-id&gt;</span></header>'
                f'<pre class="tail">(empty)</pre></div>'
            )
            continue
        tiles.append(
            f'<div class="tile">'
            f'<header><b>tile {idx}</b> {sid} <span class="meta">starting...</span></header>'
            f'<pre class="tail" data-session="{sid}">(loading...)</pre></div>'
        )
    return f"""{_PAGE_HEAD}
<div class="grid">{"".join(tiles)}</div>
{_PAGE_SCRIPT}
"""


__all__ = ["router"]
