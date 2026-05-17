"""Unit tests for the live session-peek route (#1217)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bernstein.core.agents import agent_ipc
from bernstein.core.handoff import StreamTailBuffer
from bernstein.core.routes.session_peek import router


def _make_app(workdir: Path) -> FastAPI:
    """Build a minimal FastAPI shell wired to ``workdir`` for the peek route."""
    app = FastAPI()
    app.state.workdir = workdir
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _clear_stdin_pipes() -> None:
    """Keep the IPC registry empty between tests so they don't leak state."""
    agent_ipc._stdin_pipes.clear()  # type: ignore[attr-defined]
    yield
    agent_ipc._stdin_pipes.clear()  # type: ignore[attr-defined]


def test_peek_returns_recent_tail(tmp_path: Path) -> None:
    """The endpoint returns whatever the ring buffer holds, oldest-first."""
    StreamTailBuffer(tmp_path, "sess-1").append(surface="terminal", text="hello")
    StreamTailBuffer(tmp_path, "sess-1").append(surface="terminal", text="world")

    client = TestClient(_make_app(tmp_path))
    response = client.get("/sessions/sess-1/peek")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "sess-1"
    assert [entry["text"] for entry in body["tail"]] == ["hello", "world"]


def test_peek_empty_buffer_returns_empty_tail(tmp_path: Path) -> None:
    """Missing buffers are not an error — the page just renders blank."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/sessions/sess-2/peek")
    assert response.status_code == 200
    body = response.json()
    assert body == {"session_id": "sess-2", "tail": []}


def test_peek_tail_query_caps_lines(tmp_path: Path) -> None:
    """The ``tail`` query argument bounds how many lines come back."""
    buf = StreamTailBuffer(tmp_path, "sess-3")
    for i in range(10):
        buf.append(surface="terminal", text=f"line {i}")

    client = TestClient(_make_app(tmp_path))
    response = client.get("/sessions/sess-3/peek", params={"tail": 3})
    assert response.status_code == 200
    body = response.json()
    assert [entry["text"] for entry in body["tail"]] == ["line 7", "line 8", "line 9"]


def test_peek_invalid_session_id_returns_400(tmp_path: Path) -> None:
    """Path-traversal-shaped ids are rejected before they hit the buffer."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/sessions/..%2Fsecret/peek")
    assert response.status_code in (400, 404)


def test_peek_page_renders_session_id(tmp_path: Path) -> None:
    """The single-session HTML page embeds the validated id and the poller."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/dashboard/peek/sess-html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert 'data-session="sess-html"' in body
    assert "/sessions/" in body  # Polling fetch path is wired in.


def test_peek_grid_renders_four_tiles(tmp_path: Path) -> None:
    """The grid page renders all four tiles, marking missing ones as empty."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/dashboard/peek", params={"s1": "a", "s3": "c"})
    assert response.status_code == 200
    body = response.text
    assert 'data-session="a"' in body
    assert 'data-session="c"' in body
    # Two tiles have no session id and render as placeholders.
    assert body.count("(empty)") == 2


def test_peek_grid_rejects_bad_id(tmp_path: Path) -> None:
    """One bad query value fails the grid render with 400."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/dashboard/peek", params={"s1": "../boom"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Send-bar (#1217 — POST /sessions/{id}/send)
# ---------------------------------------------------------------------------


def test_send_writes_to_registered_stdin_pipe(tmp_path: Path) -> None:
    """A valid POST forwards the line to ``agent_ipc`` and reports delivery."""
    pipe = io.BytesIO()
    agent_ipc.register_stdin_pipe("sess-send", pipe)  # type: ignore[arg-type]

    client = TestClient(_make_app(tmp_path))
    response = client.post("/sessions/sess-send/send", json={"text": "hello agent"})

    assert response.status_code == 200
    assert response.json() == {"session_id": "sess-send", "delivered": True}
    # The IPC layer JSON-wraps the payload before writing to the pipe.
    assert b'"user_message"' in pipe.getvalue()
    assert b"hello agent" in pipe.getvalue()


def test_send_returns_404_when_no_pipe_registered(tmp_path: Path) -> None:
    """If the agent has no live stdin pipe the operator gets a 404, not a 500."""
    client = TestClient(_make_app(tmp_path))
    response = client.post("/sessions/sess-cold/send", json={"text": "hi"})
    assert response.status_code == 404
    body = response.json()
    assert body["delivered"] is False
    assert body["session_id"] == "sess-cold"


def test_send_rejects_empty_text(tmp_path: Path) -> None:
    """An empty body or empty ``text`` field is a 400, never a no-op success."""
    client = TestClient(_make_app(tmp_path))
    response = client.post("/sessions/sess-x/send", json={"text": ""})
    assert response.status_code == 400


def test_send_rejects_oversize_text(tmp_path: Path) -> None:
    """Payloads larger than ``MAX_SEND_BYTES`` are rejected with 413."""
    pipe = io.BytesIO()
    agent_ipc.register_stdin_pipe("sess-big", pipe)  # type: ignore[arg-type]

    client = TestClient(_make_app(tmp_path))
    response = client.post("/sessions/sess-big/send", json={"text": "x" * 5000})
    assert response.status_code == 413
    assert pipe.getvalue() == b""  # nothing was forwarded.


def test_send_rejects_bad_session_id(tmp_path: Path) -> None:
    """Send shares the slug-validator with peek, so traversal payloads fail."""
    client = TestClient(_make_app(tmp_path))
    # ``%2F`` is rejected by Starlette before our handler runs; ``$`` is
    # routed but our validator rejects it explicitly with 400.
    response = client.post("/sessions/bad$id/send", json={"text": "hi"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# HTML surface — send-bar + search wiring
# ---------------------------------------------------------------------------


def test_single_page_includes_send_bar_and_search(tmp_path: Path) -> None:
    """Single-session page exposes a send-bar form plus a per-tile search box."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/dashboard/peek/sess-html")
    body = response.text
    assert 'class="send-bar"' in body
    assert 'placeholder="send to stdin (enter)"' in body
    assert 'data-scope="self"' in body
    # The send wiring talks to the new POST endpoint.
    assert "/sessions/" in body
    assert "/send" in body


def test_grid_page_has_send_bar_per_active_tile_and_shared_search(tmp_path: Path) -> None:
    """Grid renders one send-bar per populated tile plus one shared filter box."""
    client = TestClient(_make_app(tmp_path))
    response = client.get("/dashboard/peek", params={"s1": "a", "s2": "b"})
    body = response.text
    # Two populated tiles → two send-bar forms; empties stay form-less.
    assert body.count('class="send-bar"') == 2
    # The shared-search input element renders exactly once (the JS selector
    # string elsewhere in the page mentions ``data-scope="all"`` too, which
    # is why we look for the full input-element tag instead).
    assert '<input class="search" type="search" placeholder="filter all tiles (regex)" data-scope="all"' in body
    assert 'data-session="a"' in body
    assert 'data-session="b"' in body
