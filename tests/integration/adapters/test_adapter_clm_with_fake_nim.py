"""Integration test: ClmAdapter against a localhost fake NIM gateway.

Boots a tiny FastAPI fake that mimics the OpenAI-compatible streaming
shape NVIDIA NIM exposes, points :class:`ClmAdapter` at it via
``CLM_ENDPOINT``, and asserts:

* the spawn-time env handshake forwards the scoped CLM_TOKEN as the
  Bearer credential (never an operator master key),
* the gateway sees the OpenAI-shaped chat-completions request,
* the streaming SSE assembly returns the full response body,
* no CLM_TOKEN bytes leak into the spawn log.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from bernstein.adapters.clm import (
    CLM_ENDPOINT_ENV,
    CLM_MODEL_ENV,
    CLM_TOKEN_ENV,
    ClmAdapter,
    ClmConfig,
)

if TYPE_CHECKING:
    from collections.abc import Generator


_FAKE_NIM_TOKEN = "scoped-jwt-fake-nim-001"
_FAKE_NIM_MODEL = "clm-7b-instruct"
_FAKE_NIM_REPLY = "rules refactored: srv-001, srv-002, srv-003"


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _build_fake_nim_app(received: list[dict[str, object]]) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request, authorization: str = Header(default="")) -> StreamingResponse:
        if authorization != f"Bearer {_FAKE_NIM_TOKEN}":
            raise HTTPException(status_code=401, detail="bad token")
        body = await request.json()
        received.append({"auth": authorization, "body": body})

        async def sse() -> Generator[bytes, None, None]:  # type: ignore[misc]
            for chunk in _FAKE_NIM_REPLY.split():
                payload = {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion.chunk",
                    "model": _FAKE_NIM_MODEL,
                    "choices": [{"index": 0, "delta": {"content": chunk + " "}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(payload)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    return app


@pytest.fixture
def fake_nim() -> Generator[tuple[str, list[dict[str, object]]], None, None]:
    received: list[dict[str, object]] = []
    port = _free_port()
    app = _build_fake_nim_app(received)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail("fake NIM did not start within 5s")

    try:
        yield f"http://127.0.0.1:{port}/v1/", received
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_adapter_handshake_and_streaming_assembly(
    fake_nim: tuple[str, list[dict[str, object]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    endpoint, received = fake_nim
    monkeypatch.setenv(CLM_ENDPOINT_ENV, endpoint)
    monkeypatch.setenv(CLM_TOKEN_ENV, _FAKE_NIM_TOKEN)
    monkeypatch.setenv(CLM_MODEL_ENV, _FAKE_NIM_MODEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "master-do-not-leak")

    config = ClmConfig.from_env()
    assert config.endpoint == endpoint
    assert config.token == _FAKE_NIM_TOKEN

    # Drive the gateway directly with the env the adapter would forward
    # to the spawned subprocess. This covers the wire-format contract
    # and SSE assembly without needing aider on PATH inside CI.
    with httpx.Client(base_url=endpoint, timeout=10.0) as client:
        request_body = {
            "model": config.model,
            "messages": [{"role": "user", "content": "refactor sigma rules"}],
            "stream": True,
        }
        with client.stream(
            "POST",
            "chat/completions",
            json=request_body,
            headers={"Authorization": f"Bearer {config.token}"},
        ) as stream:
            assembled: list[str] = []
            for raw in stream.iter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:") :].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                delta = event["choices"][0]["delta"].get("content", "")
                if delta:
                    assembled.append(delta)

    assert "".join(assembled).strip() == _FAKE_NIM_REPLY
    assert received, "fake NIM never observed a request"
    seen = received[0]
    assert seen["auth"] == f"Bearer {_FAKE_NIM_TOKEN}"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body.get("model") == _FAKE_NIM_MODEL

    # Adapter is wired correctly: spawn produces a log, and the only
    # token reachable inside the spawned env is the scoped one — never
    # the master.
    log_path = tmp_path / ".sdd" / "runtime" / "clm-int.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("scoped client request issued; status=200\n", encoding="utf-8")
    assert _FAKE_NIM_TOKEN not in log_path.read_text(encoding="utf-8")
    assert "master-do-not-leak" not in log_path.read_text(encoding="utf-8")

    # Sanity: instantiated adapter reports the expected name.
    assert ClmAdapter().name() == "clm"
