"""Unit tests for the lineage tamper-alert sinks."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from bernstein.core.observability.lineage_alert import (
    LineageAlertSink,
    LineageTamperEvent,
    NullAlertSink,
    WebhookAlertSink,
    sink_from_config,
)


def _event() -> LineageTamperEvent:
    return LineageTamperEvent(
        run_id="run-1",
        errors=["chain broken at seq 4"],
        record_count=10,
        detected_at=1700000000.0,
    )


class _RecordingServer(HTTPServer):
    received: list[bytes]
    status_codes: list[int]
    cursor: int

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(addr, handler)
        self.received = []
        self.status_codes = [200]
        self.cursor = 0


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        server = self.server
        assert isinstance(server, _RecordingServer)
        server.received.append(body)
        idx = min(server.cursor, len(server.status_codes) - 1)
        status = server.status_codes[idx]
        server.cursor += 1
        self.send_response(status)
        self.end_headers()
        if 200 <= status < 300:
            self.wfile.write(b"ok")

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def fake_siem() -> Any:
    server = _RecordingServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


class TestWebhookAlertSink:
    def test_emit_success_sends_json_payload(self, fake_siem: _RecordingServer) -> None:
        url = f"http://127.0.0.1:{fake_siem.server_address[1]}/hec"
        sink = WebhookAlertSink(url, timeout_secs=2.0, max_retries=0)
        assert sink.emit(_event())

        assert len(fake_siem.received) == 1
        payload = json.loads(fake_siem.received[0].decode())
        assert payload["type"] == "lineage_tamper_detected"
        assert payload["run_id"] == "run-1"
        assert payload["errors"] == ["chain broken at seq 4"]
        assert payload["record_count"] == 10

    def test_emit_retries_on_5xx_then_succeeds(self, fake_siem: _RecordingServer) -> None:
        fake_siem.status_codes = [503, 503, 200]
        url = f"http://127.0.0.1:{fake_siem.server_address[1]}/hec"
        sink = WebhookAlertSink(url, timeout_secs=2.0, max_retries=3, backoff_secs=0.0)
        t0 = time.monotonic()
        ok = sink.emit(_event())
        assert ok
        assert len(fake_siem.received) == 3
        assert time.monotonic() - t0 < 3.0

    def test_emit_fails_closed_after_retry_budget(self, fake_siem: _RecordingServer) -> None:
        fake_siem.status_codes = [500, 500, 500, 500, 500]
        url = f"http://127.0.0.1:{fake_siem.server_address[1]}/hec"
        sink = WebhookAlertSink(url, timeout_secs=2.0, max_retries=2, backoff_secs=0.0)
        assert sink.emit(_event()) is False
        assert len(fake_siem.received) == 3  # initial + 2 retries

    def test_4xx_does_not_retry(self, fake_siem: _RecordingServer) -> None:
        fake_siem.status_codes = [403]
        url = f"http://127.0.0.1:{fake_siem.server_address[1]}/hec"
        sink = WebhookAlertSink(url, timeout_secs=2.0, max_retries=5, backoff_secs=0.0)
        assert sink.emit(_event()) is False
        assert len(fake_siem.received) == 1

    def test_broken_endpoint_returns_false_no_raise(self) -> None:
        sink = WebhookAlertSink(
            "http://127.0.0.1:1/never-listening",
            timeout_secs=0.2,
            max_retries=1,
            backoff_secs=0.0,
        )
        assert sink.emit(_event()) is False

    def test_custom_headers_passed_through(self, fake_siem: _RecordingServer) -> None:
        url = f"http://127.0.0.1:{fake_siem.server_address[1]}/hec"
        sink = WebhookAlertSink(
            url,
            headers={"Authorization": "Splunk abc123"},
            timeout_secs=2.0,
            max_retries=0,
        )
        assert sink.emit(_event())

    def test_satisfies_protocol(self) -> None:
        assert isinstance(WebhookAlertSink("http://x"), LineageAlertSink)
        assert isinstance(NullAlertSink(), LineageAlertSink)


class TestSinkFromConfig:
    def test_disabled_returns_null(self) -> None:
        sink = sink_from_config(enabled=False, webhook_url="http://x")
        assert isinstance(sink, NullAlertSink)
        assert sink.emit(_event())  # null sink swallows successfully

    def test_missing_url_returns_null(self) -> None:
        sink = sink_from_config(enabled=True, webhook_url=None)
        assert isinstance(sink, NullAlertSink)

    def test_full_config_returns_webhook(self) -> None:
        sink = sink_from_config(enabled=True, webhook_url="http://siem.example/hec")
        assert isinstance(sink, WebhookAlertSink)
        assert sink.url == "http://siem.example/hec"


class TestLineageTamperEvent:
    def test_extra_field_round_trips(self, fake_siem: _RecordingServer) -> None:
        url = f"http://127.0.0.1:{fake_siem.server_address[1]}/hec"
        sink = WebhookAlertSink(url, timeout_secs=2.0, max_retries=0)
        event = LineageTamperEvent(
            run_id="r2",
            errors=[],
            record_count=0,
            detected_at=42.0,
            source="verify-cli",
            extra={"correlation_id": "abc"},
        )
        assert sink.emit(event)
        payload = json.loads(fake_siem.received[0].decode())
        assert payload["source"] == "verify-cli"
        assert payload["extra"] == {"correlation_id": "abc"}
