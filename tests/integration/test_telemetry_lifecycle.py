"""Integration tests for the full first-run telemetry lifecycle.

Covers opt-in / opt-out flips, mock receiver, install id lifecycle,
first-run notice idempotence, and queue persistence.
"""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from bernstein.core.telemetry import (
    Client,
    DailyActivePayload,
    ErrorCategory,
    TelemetryEvent,
    install_id_path,
    queue_path,
    write_enabled,
)
from bernstein.core.telemetry.wire import (
    FirstRunTimer,
    emit_command_invoked,
    emit_first_run_completed,
    emit_first_run_started,
    maybe_print_first_run_notice,
)


class _MockReceiver:
    """Minimal HTTP receiver that captures every event body."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _MockReceiver:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                try:
                    receiver.events.append(json.loads(body))
                except json.JSONDecodeError:
                    receiver.events.append({"_raw": body})
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_a: Any, **_kw: Any) -> None:
                pass

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def endpoint(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1/events"

    def __exit__(self, *_a: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / ".bernstein").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _client_for(home: Path, endpoint: str) -> Client:
    return Client(env={}, home=home, endpoint=endpoint, http_client=httpx.Client())


# ---------------------------------------------------------------------------
# Lifecycle scenarios
# ---------------------------------------------------------------------------


def test_full_first_run_flow_with_mock_receiver(home: Path) -> None:
    """First-time operator: notice -> opt-in -> first run -> events delivered."""
    # 1. Default off.  Notice prints once.
    buf = io.StringIO()
    maybe_print_first_run_notice(home=home, out=buf)
    assert "collects no telemetry" in buf.getvalue()

    # 2. Operator runs `bernstein telemetry on`.
    write_enabled(True, home=home)

    # 3. First run emits start + complete via FirstRunTimer.
    with _MockReceiver() as recv:
        client = _client_for(home, recv.endpoint)
        try:
            with FirstRunTimer(time_since_install_seconds=10, client=client) as timer:
                # simulate the body of the run
                emit_command_invoked(name_only="run", client=client)
                timer.set_error(ErrorCategory.AUTH_FAILED)
        finally:
            client.close()

    names = [e["name"] for e in recv.events]
    assert "first_run_started" in names
    assert "first_run_completed" in names
    assert "command_invoked" in names
    completed = next(e for e in recv.events if e["name"] == "first_run_completed")
    assert completed["payload"]["ok"] is False
    assert completed["payload"]["error_category"] == "auth_failed"


def test_opt_in_then_flip_off(home: Path) -> None:
    write_enabled(True, home=home)
    with _MockReceiver() as recv:
        client = _client_for(home, recv.endpoint)
        try:
            emit_first_run_started(time_since_install_seconds=0, client=client)
        finally:
            client.close()
        before = len(recv.events)
        # Flip off mid-session.
        write_enabled(False, home=home)
        client2 = _client_for(home, recv.endpoint)
        try:
            emit_first_run_completed(ok=True, duration_ms=1, client=client2)
        finally:
            client2.close()
        after = len(recv.events)
    assert before == 1
    assert after == 1  # no new events after opt-out


def test_opt_out_then_flip_on(home: Path) -> None:
    write_enabled(False, home=home)
    with _MockReceiver() as recv:
        client = _client_for(home, recv.endpoint)
        try:
            emit_first_run_started(time_since_install_seconds=0, client=client)
        finally:
            client.close()
        assert recv.events == []
        write_enabled(True, home=home)
        client2 = _client_for(home, recv.endpoint)
        try:
            emit_first_run_started(time_since_install_seconds=5, client=client2)
        finally:
            client2.close()
    assert len(recv.events) == 1


def test_install_id_is_not_created_until_opt_in(home: Path) -> None:
    with _MockReceiver() as recv:
        client = _client_for(home, recv.endpoint)
        try:
            emit_first_run_started(time_since_install_seconds=0, client=client)
        finally:
            client.close()
    assert not install_id_path(home=home).exists()
    assert recv.events == []


def test_queue_mirrors_emitted_events(home: Path) -> None:
    write_enabled(True, home=home)
    with _MockReceiver() as recv:
        client = _client_for(home, recv.endpoint)
        try:
            for _ in range(3):
                client.emit(
                    TelemetryEvent.DAILY_ACTIVE,
                    DailyActivePayload(day_iso="2026-05-17"),
                )
        finally:
            client.close()
    lines = queue_path(home=home).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        assert json.loads(line)["name"] == "daily_active"
    assert len(recv.events) == 3
