"""Live terminal-peek route for the dashboard (#1217).

Operators want to glance at a running agent's recent stdout/stderr from a
browser without having to ``bernstein attach`` over SSH. This module
delivers the feature in three endpoints that together satisfy the
acceptance criteria on #1217:

* ``GET /sessions/{session_id}/peek`` — returns the last N entries of the
  session's stream-tail ring buffer as JSON.  Polled by the static page
  on a short interval (<500 ms perceived latency on loopback).
* ``POST /sessions/{session_id}/send`` — pipe one line of input back to
  the running agent's stdin via :mod:`bernstein.core.agents.agent_ipc`.
  This is the send-bar primitive; the bearer-auth middleware in
  :mod:`bernstein.core.server.server_middleware` covers it like every
  other mutating route.
* ``GET /dashboard/peek/{session_id}`` — vanilla-JS HTML page that polls
  the JSON endpoint, renders the lines, exposes a send-bar, and supports
  client-side regex filtering of the buffered tail.
* ``GET /dashboard/peek`` — 2x2 tile grid that watches up to four
  sessions side-by-side; session ids come in via the
  ``s1`` / ``s2`` / ``s3`` / ``s4`` query parameters.  Each tile carries
  its own send-bar; a single shared search box filters every tile.

Deferred (tracked on #1217 for follow-up slices):

* WebSocket streaming.  Short-interval polling stays inside the <500 ms
  perceived-latency budget on loopback and side-steps proxy / corporate
  firewall edge-cases that block long-lived WS connections.
* xterm.js terminal rendering.  ANSI / cursor-positioning fidelity is
  not part of the acceptance criteria; ``<pre>`` is sufficient and
  keeps the page dependency-free.

The buffer source is the same ``StreamTailBuffer`` the handoff route
reads, so the JSON shape matches ``GET /handoff/{token}#tail`` and the
page can reuse the rendering logic from the handoff page if a future
slice replaces the ``<pre>`` with an xterm.js surface.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Final

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bernstein.core.agents.agent_ipc import has_stdin_pipe, send_message
from bernstein.core.handoff import StreamTailBuffer

router = APIRouter()

# Hard caps mirror the on-disk ring buffer cap so callers can't DoS by
# asking for absurd tail sizes. ``DEFAULT_TAIL`` matches the issue's
# "default 200 lines" guidance.
DEFAULT_TAIL: Final[int] = 200
MAX_TAIL: Final[int] = 500
# Send-bar payload cap. A single operator-typed line; long enough for a
# realistic slash command but short enough that we never block on a
# pathological body. Mirrors the chat-bridge per-message ceiling.
MAX_SEND_BYTES: Final[int] = 4096
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


@router.post("/sessions/{session_id}/send")
def send_to_session(
    session_id: str,
    request: Request,
    payload: dict[str, str] = Body(default_factory=dict),  # noqa: B008
) -> JSONResponse:
    """Pipe one line of operator input into ``session_id``'s stdin.

    The send-bar tile on the dashboard POSTs ``{"text": "..."}`` here; we
    forward through :func:`bernstein.core.agents.agent_ipc.send_message`,
    which writes the line into the agent's registered stdin pipe.

    Args:
        session_id: Slug-shaped session id; must pass the same validator
            as the peek endpoint.
        request: FastAPI request (unused beyond routing-level checks but
            present so the bearer-auth middleware sees the same shape as
            our other mutating routes).
        payload: JSON body with a single ``text`` field. Empty / missing
            text is rejected with ``400``; oversize payloads above
            :data:`MAX_SEND_BYTES` are rejected with ``413``.

    Returns:
        JSON envelope with ``session_id`` and ``delivered`` (``True`` if
        the line reached a registered stdin pipe, ``False`` if no pipe
        is registered for this session).  The 200/404 split lets the
        front-end keep the input enabled but warn the operator when the
        agent has no live pipe yet.
    """
    safe_id = _validate_session_id(session_id)
    _ = request  # parity with peek_session — keeps signature uniform.
    text = (payload.get("text") or "").rstrip("\r\n")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text.encode("utf-8")) > MAX_SEND_BYTES:
        raise HTTPException(status_code=413, detail="text too large")
    if not has_stdin_pipe(safe_id):
        return JSONResponse(
            status_code=404,
            content={"session_id": safe_id, "delivered": False, "reason": "no stdin pipe"},
        )
    delivered = send_message(safe_id, text)
    return JSONResponse({"session_id": safe_id, "delivered": delivered})


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
           display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  header b { color: #f1f5f9; }
  header .meta { color: #6b7785; font-size: 11px; margin-left: auto; }
  header input.search { background: #0b0d10; border: 1px solid #232b34; color: #cbd2da;
                        padding: 2px 6px; font: inherit; min-width: 0; flex: 1 1 120px;
                        max-width: 240px; border-radius: 3px; }
  header input.search:focus { outline: none; border-color: #4f46e5; }
  pre.tail { margin: 0; padding: 8px 12px; white-space: pre-wrap; word-break: break-word;
             flex: 1 1 auto; overflow-y: auto; min-height: 0; }
  .page { display: flex; flex-direction: column; height: 100vh; }
  .send-bar { display: flex; gap: 6px; padding: 6px 8px; background: #141a21;
              border-top: 1px solid #232b34; align-items: center; }
  .send-bar input.line { flex: 1 1 auto; background: #0b0d10; border: 1px solid #232b34;
                         color: #cbd2da; padding: 4px 8px; font: inherit; border-radius: 3px; }
  .send-bar input.line:focus { outline: none; border-color: #4f46e5; }
  .send-bar button { background: #4f46e5; color: white; border: 0; padding: 4px 10px;
                     font: inherit; border-radius: 3px; cursor: pointer; }
  .send-bar button:disabled { opacity: 0.4; cursor: not-allowed; }
  .send-bar .status { color: #6b7785; font-size: 11px; min-width: 60px; text-align: right; }
  .send-bar .status.ok { color: #4ade80; }
  .send-bar .status.err { color: #ef4444; }
  .grid { display: grid; gap: 6px; height: 100vh; padding: 6px; box-sizing: border-box;
          grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; background: #05070a; }
  .grid-wrap { display: flex; flex-direction: column; height: 100vh; }
  .grid-wrap > .grid { flex: 1 1 auto; height: auto; }
  .grid-header { display: flex; gap: 8px; align-items: center; padding: 6px 10px;
                 background: #141a21; border-bottom: 1px solid #232b34; flex-wrap: wrap; }
  .grid-header b { color: #f1f5f9; }
  .tile { display: flex; flex-direction: column; min-height: 0; background: #0b0d10;
          border: 1px solid #232b34; border-radius: 4px; overflow: hidden; }
  .tile.empty pre.tail { color: #4b5563; }
  .err { color: #ef4444; }
  /* Phone viewport: stack tiles vertically below ~480px so each tile is scrollable
     on a 390x844 iPhone 13. Keeps the 4-tile spec usable on the device the issue
     explicitly calls out. */
  @media (max-width: 480px) {
    .grid { grid-template-columns: 1fr; grid-template-rows: repeat(4, minmax(160px, 1fr));
            height: auto; min-height: 100vh; }
    .grid-wrap { height: auto; min-height: 100vh; }
  }
</style>
"""

_PAGE_SCRIPT = """\
<script>
(function () {
  // Poll cadence — 400 ms keeps perceived latency inside the <500 ms budget
  // the issue calls out for the loopback case, without flooding the server
  // when several tiles render at once.
  const POLL_MS = 400;
  // Per-tile state keyed by session id so the shared search box and the
  // poll loops can co-operate without re-fetching on every keystroke.
  const tileState = new Map();

  function renderTile(state) {
    const filter = state.filterRegex;
    const lines = state.lines;
    const visible = filter
      ? lines.filter(function (line) { try { return filter.test(line); } catch (_) { return true; } })
      : lines;
    state.out.textContent = visible.length ? visible.join('\\n') : '(buffer empty)';
    state.out.scrollTop = state.out.scrollHeight;
    if (state.meta) {
      state.meta.textContent = filter
        ? visible.length + '/' + lines.length + ' lines'
        : lines.length + ' lines';
    }
  }

  function attach(node) {
    const id = node.dataset.session;
    if (!id) return;
    const out = node.querySelector('pre.tail');
    const meta = node.querySelector('.meta');
    const state = { id: id, out: out, meta: meta, lines: [], filterRegex: null };
    tileState.set(id, state);

    const sendForm = node.querySelector('form.send-bar');
    if (sendForm) {
      const input = sendForm.querySelector('input.line');
      const button = sendForm.querySelector('button');
      const status = sendForm.querySelector('.status');
      sendForm.addEventListener('submit', async function (evt) {
        evt.preventDefault();
        const text = input.value;
        if (!text) return;
        button.disabled = true;
        if (status) { status.textContent = 'sending...'; status.className = 'status'; }
        try {
          const res = await fetch('/sessions/' + encodeURIComponent(id) + '/send', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text }),
          });
          const body = await res.json().catch(function () { return {}; });
          if (res.ok && body.delivered) {
            input.value = '';
            if (status) { status.textContent = 'sent'; status.className = 'status ok'; }
          } else if (res.status === 404) {
            if (status) { status.textContent = 'no pipe'; status.className = 'status err'; }
          } else {
            if (status) { status.textContent = 'err ' + res.status; status.className = 'status err'; }
          }
        } catch (err) {
          if (status) { status.textContent = 'error'; status.className = 'status err'; }
        } finally {
          button.disabled = false;
          input.focus();
        }
      });
    }

    async function tick() {
      try {
        const res = await fetch('/sessions/' + encodeURIComponent(id) + '/peek?tail=200',
                                { credentials: 'same-origin' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const body = await res.json();
        state.lines = (body.tail || []).map(function (e) { return e.text; });
        state.out.classList.remove('err');
        renderTile(state);
      } catch (err) {
        if (state.meta) state.meta.textContent = 'error: ' + err.message;
        state.out.classList.add('err');
      }
      setTimeout(tick, POLL_MS);
    }
    tick();
  }

  function bindSearch(input, scope) {
    if (!input) return;
    function apply() {
      const raw = input.value;
      let rx = null;
      if (raw) {
        try { rx = new RegExp(raw, 'i'); input.classList.remove('err'); }
        catch (_) { input.classList.add('err'); return; }
      } else {
        input.classList.remove('err');
      }
      if (scope === 'all') {
        tileState.forEach(function (s) { s.filterRegex = rx; renderTile(s); });
      } else if (scope && scope.dataset && scope.dataset.session) {
        const s = tileState.get(scope.dataset.session);
        if (s) { s.filterRegex = rx; renderTile(s); }
      }
    }
    input.addEventListener('input', apply);
  }

  document.querySelectorAll('[data-session]').forEach(attach);
  document.querySelectorAll('input.search[data-scope="all"]').forEach(function (inp) {
    bindSearch(inp, 'all');
  });
  document.querySelectorAll('input.search[data-scope="self"]').forEach(function (inp) {
    const tile = inp.closest('[data-session]');
    if (tile) bindSearch(inp, tile);
  });
})();
</script>
"""


def _render_single_page(safe_id: str) -> str:
    """Render the single-session peek page. ``safe_id`` is HTML-escaped.

    Wraps the tail viewer, a per-tile regex search box, and the send-bar
    in one column.  The DOM exposes ``data-session`` on the tile root so
    the inline script can poll, search, and send for the same id.
    """
    return f"""{_PAGE_HEAD}
<div class="page" data-session="{safe_id}">
  <header>
    <b>peek</b> {safe_id}
    <input class="search" type="search" placeholder="filter (regex)" data-scope="self"
           aria-label="filter buffer">
    <span class="meta">starting...</span>
  </header>
  <pre class="tail" data-session="{safe_id}">(loading...)</pre>
  <form class="send-bar" autocomplete="off">
    <input class="line" type="text" placeholder="send to stdin (enter)" aria-label="send line">
    <button type="submit">send</button>
    <span class="status">idle</span>
  </form>
</div>
{_PAGE_SCRIPT}
"""


def _render_grid_page(safe_ids: list[str | None]) -> str:
    """Render the 2x2 tile page. ``safe_ids`` entries are pre-escaped or None.

    A single top-level search box filters every active tile at once; each
    populated tile carries its own send-bar so operators can target a
    specific agent without leaving the grid view.
    """
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
            f'<div class="tile" data-session="{sid}">'
            f'<header><b>tile {idx}</b> {sid} <span class="meta">starting...</span></header>'
            f'<pre class="tail" data-session="{sid}">(loading...)</pre>'
            f'<form class="send-bar" autocomplete="off">'
            f'<input class="line" type="text" placeholder="send (enter)" aria-label="send line">'
            f'<button type="submit">send</button>'
            f'<span class="status">idle</span>'
            f"</form></div>"
        )
    return f"""{_PAGE_HEAD}
<div class="grid-wrap">
  <div class="grid-header">
    <b>peek</b>
    <input class="search" type="search" placeholder="filter all tiles (regex)" data-scope="all"
           aria-label="filter buffers">
  </div>
  <div class="grid">{"".join(tiles)}</div>
</div>
{_PAGE_SCRIPT}
"""


__all__ = ["router"]
