"""Unit tests for the live session-peek route (#1217)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bernstein.core.handoff import StreamTailBuffer
from bernstein.core.routes.session_peek import router


def _make_app(workdir: Path) -> FastAPI:
    """Build a minimal FastAPI shell wired to ``workdir`` for the peek route."""
    app = FastAPI()
    app.state.workdir = workdir
    app.include_router(router)
    return app


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
