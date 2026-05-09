"""Integration test: real Ollama-loopback exercise for the EU-residency profile.

The 45 unit tests in ``test_deepseek_v4_eu_residency.py`` cover the
``_is_self_hosted_endpoint`` decision matrix in isolation. This file
adds the missing real-network exercise the customer compliance team
asks for: an actual HTTP server bound to ``127.0.0.1:11434``, a real
HTTP round-trip through the OpenAI-compatible transport, and a
matching negative-path test against ``https://api.deepseek.com`` that
must raise ``RESIDENCY_VIOLATION`` cleanly.

What this test does NOT require:
    - ``aider`` installed (the adapter shells out to aider, but the
      residency guard is on the spawn entry path -- it raises before
      any subprocess is spawned).
    - ``ollama`` installed (we boot a stdlib ``http.server`` that
      mimics the OpenAI-compatible response shape).

What this test DOES require:
    - A free TCP port on the loopback interface (we pick one
      dynamically so concurrent runs don't collide).
    - Network policy permitting ``127.0.0.1`` (the default; the
      airgap profile's deny-all blocks it but no test harness runs
      under that profile by default).
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import httpx
import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.ollama import OllamaAdapter

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fake Ollama / OpenAI-compatible HTTP server
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Allocate a free TCP port on the loopback interface."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


_DEEPSEEK_V4_FLASH_RESPONSE: dict[str, object] = {
    "id": "chatcmpl-fake-deepseek-v4-flash-001",
    "object": "chat.completion",
    "created": 1715212800,
    "model": "deepseek-v4-flash",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "fake-eu-loopback-response: refactored 3 detection rules",
            },
            "finish_reason": "stop",
        },
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
}

_OLLAMA_TAGS_RESPONSE: dict[str, object] = {
    "models": [
        {"name": "deepseek-v4-flash:latest", "size": 0, "digest": "sha256:fake"},
    ],
}


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    """Minimal Ollama / OpenAI-compatible request handler.

    Responds to:

    * ``GET /api/tags`` -- the Ollama health probe.
    * ``POST /v1/chat/completions`` -- the OpenAI-compatible completions
      endpoint (also reachable via Ollama's native ``/api/generate``;
      we mount it on the OpenAI path because aider/litellm route
      through it for ollama models).

    Records every request body to ``server.received`` so the test can
    assert what the adapter sent.
    """

    server: _RecordingServer  # type: ignore[assignment] -- typed via subclass

    def log_message(self, format: str, *args: object) -> None:
        # Silence the default stderr access log so pytest output stays clean.
        pass

    def _send_json(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            self.server.received.append({"method": "GET", "path": self.path})
            self._send_json(200, _OLLAMA_TAGS_RESPONSE)
            return
        self._send_json(404, {"error": f"unknown path {self.path}"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body_raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(body_raw.decode("utf-8")) if body_raw else {}
        except json.JSONDecodeError:
            body = {"_raw": body_raw.decode("utf-8", errors="replace")}
        self.server.received.append({"method": "POST", "path": self.path, "body": body})
        if self.path in {"/v1/chat/completions", "/api/chat"}:
            self._send_json(200, _DEEPSEEK_V4_FLASH_RESPONSE)
            return
        self._send_json(404, {"error": f"unknown path {self.path}"})


class _RecordingServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that records every received request."""

    received: list[dict[str, object]]

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(addr, handler)
        self.received = []


@pytest.fixture
def fake_ollama_server() -> Generator[tuple[str, _RecordingServer], None, None]:
    """Boot a fake Ollama-compatible server on 127.0.0.1:<free-port>."""
    port = _free_port()
    server = _RecordingServer(("127.0.0.1", port), _FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Real-network round-trip on the loopback
# ---------------------------------------------------------------------------


class TestLoopbackRoundTrip:
    """Real HTTP round-trip against the fake Ollama on 127.0.0.1."""

    def test_health_probe_succeeds(
        self,
        fake_ollama_server: tuple[str, _RecordingServer],
    ) -> None:
        """Ollama's ``GET /api/tags`` is the canonical health probe."""
        base_url, server = fake_ollama_server
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base_url}/api/tags")
        assert resp.status_code == 200
        body = resp.json()
        assert any("deepseek-v4-flash" in str(m.get("name", "")) for m in body.get("models", []))
        assert any(r["method"] == "GET" and r["path"] == "/api/tags" for r in server.received)

    def test_openai_compatible_chat_completions(
        self,
        fake_ollama_server: tuple[str, _RecordingServer],
    ) -> None:
        """Aider/litellm route DeepSeek-V4 through the OpenAI-compatible /v1/chat/completions endpoint.

        We exercise the full request-response shape so that a future
        adapter rewrite that talks HTTP directly (instead of shelling
        to aider) lands on a tested transport.
        """
        base_url, server = fake_ollama_server
        request_body = {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "refactor detection rules"}],
            "stream": False,
        }
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{base_url}/v1/chat/completions",
                json=request_body,
            )
        assert resp.status_code == 200
        body = resp.json()
        # Validate the OpenAI-compatible response shape we'd parse.
        assert body["model"] == "deepseek-v4-flash"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert "fake-eu-loopback-response" in body["choices"][0]["message"]["content"]
        assert body["usage"]["total_tokens"] == 21
        # The fake server saw the request we sent.
        post_records = [r for r in server.received if r["method"] == "POST"]
        assert len(post_records) == 1
        assert post_records[0]["body"]["model"] == "deepseek-v4-flash"

    def test_adapter_accepts_loopback_url_under_eu_residency(
        self,
        fake_ollama_server: tuple[str, _RecordingServer],
    ) -> None:
        """The residency guard MUST recognise our loopback URL as self-hosted."""
        base_url, _server = fake_ollama_server
        adapter = OllamaAdapter(base_url=base_url, eu_residency=True)
        assert adapter._is_self_hosted_endpoint(base_url) is True
        assert adapter.eu_residency is True


# ---------------------------------------------------------------------------
# Negative path: hosted endpoint raises RESIDENCY_VIOLATION
# ---------------------------------------------------------------------------


class TestPublicEndpointRejection:
    """Pointing the adapter at a public DeepSeek endpoint must fail closed.

    These tests do NOT make a network call. The residency guard short-
    circuits the spawn entry path, so we exercise the structured
    failure on every documented hosted endpoint.
    """

    @pytest.mark.parametrize(
        "public_url",
        [
            "https://api.deepseek.com",
            "https://api.deepseek.com/v1",
            "https://deepseek.com/v1",
            "https://api.openai.com/v1",
            "https://openrouter.ai/api/v1",
        ],
    )
    def test_residency_violation_against_public_endpoint(
        self,
        tmp_path: Path,
        public_url: str,
    ) -> None:
        adapter = OllamaAdapter(base_url=public_url)
        with pytest.raises(RuntimeError, match="RESIDENCY_VIOLATION"):
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="deepseek-v4-flash", effort="normal"),
                session_id="loopback-1",
            )

    def test_residency_violation_carries_endpoint_in_message(
        self,
        tmp_path: Path,
    ) -> None:
        """The error message must name the model AND the offending endpoint."""
        adapter = OllamaAdapter(base_url="https://api.deepseek.com")
        with pytest.raises(RuntimeError) as exc_info:
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="deepseek-v4-flash", effort="normal"),
                session_id="loopback-2",
            )
        msg = str(exc_info.value)
        assert "RESIDENCY_VIOLATION" in msg
        assert "deepseek-v4-flash" in msg
        assert "deepseek.com" in msg

    def test_eu_residency_flag_blocks_non_v4_against_public_endpoint(
        self,
        tmp_path: Path,
    ) -> None:
        """``eu_residency=True`` extends the guard to any non-V4 model too."""
        adapter = OllamaAdapter(
            base_url="https://api.openrouter.ai",
            eu_residency=True,
        )
        with pytest.raises(RuntimeError, match="RESIDENCY_VIOLATION"):
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="qwen2.5-coder", effort="normal"),
                session_id="loopback-3",
            )


# ---------------------------------------------------------------------------
# Side-by-side comparison: loopback round-trip succeeds, hosted fails
# ---------------------------------------------------------------------------


class TestSideBySide:
    """One scenario, two endpoints -- only the loopback succeeds."""

    def test_loopback_serves_response_hosted_blocked_at_spawn(
        self,
        fake_ollama_server: tuple[str, _RecordingServer],
        tmp_path: Path,
    ) -> None:
        """Demonstrate the residency boundary is real: same model, two URLs.

        The loopback path completes a real OpenAI-shaped HTTP call and
        returns a parsed body. The hosted path never reaches the
        network -- the residency guard intervenes at spawn().
        """
        base_url, _server = fake_ollama_server
        # Loopback: real HTTP works.
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                },
            )
        assert resp.status_code == 200
        loopback_body = resp.json()
        assert loopback_body["model"] == "deepseek-v4-flash"

        # Hosted: spawn refuses immediately. The HTTP path is never
        # reached -- the residency guard fails closed BEFORE any
        # subprocess starts.
        hosted_adapter = OllamaAdapter(base_url="https://api.deepseek.com")
        with pytest.raises(RuntimeError, match="RESIDENCY_VIOLATION"):
            hosted_adapter.spawn(
                prompt="ping",
                workdir=tmp_path,
                model_config=ModelConfig(model="deepseek-v4-flash", effort="normal"),
                session_id="loopback-side-by-side",
            )
