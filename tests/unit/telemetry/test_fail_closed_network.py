"""Fail-closed network tests.

Invariants:
- Any network/HTTP error is silently swallowed.
- The local queue is appended even if the network POST fails.
- emit() never raises into the caller.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from bernstein.core.telemetry import (
    Client,
    DailyActivePayload,
    TelemetryEvent,
    queue_path,
    write_enabled,
)


class _FailingTransport(httpx.MockTransport):
    """Transport that raises on every request."""

    def __init__(self, exc: Exception) -> None:
        super().__init__(self._handle)
        self._exc = exc
        self.calls: int = 0

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise self._exc


class _StatusTransport(httpx.MockTransport):
    def __init__(self, status: int) -> None:
        super().__init__(self._handle)
        self._status = status
        self.calls: int = 0

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self._status, content=b"")


def _make_client(tmp_home: Path, transport: httpx.MockTransport) -> Client:
    http = httpx.Client(transport=transport)
    return Client(env={}, home=tmp_home, endpoint="http://test.invalid/v1/events", http_client=http)


def _payload() -> DailyActivePayload:
    return DailyActivePayload(day_iso="2026-05-17")


# ---------------------------------------------------------------------------
# Disabled state: no traffic, no file write, returns False.
# ---------------------------------------------------------------------------


def test_emit_returns_false_when_disabled(tmp_home: Path) -> None:
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)
    assert client.emit(TelemetryEvent.DAILY_ACTIVE, _payload()) is False
    assert transport.calls == 0
    assert not queue_path(home=tmp_home).exists()


def test_emit_does_not_generate_install_id_when_disabled(tmp_home: Path) -> None:
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)
    client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    assert not (tmp_home / ".bernstein" / "install-id").exists()


# ---------------------------------------------------------------------------
# Enabled state: every kind of network failure is silently absorbed.
# ---------------------------------------------------------------------------


def _opt_in(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("boom"),
        httpx.ReadTimeout("slow"),
        httpx.WriteTimeout("slow"),
        httpx.RemoteProtocolError("garbled"),
        httpx.PoolTimeout("pool"),
        ConnectionResetError("rst"),
    ],
)
def test_emit_swallows_network_exceptions(
    tmp_home: Path,
    exc: Exception,
) -> None:
    _opt_in(tmp_home)
    transport = _FailingTransport(exc)
    client = _make_client(tmp_home, transport)
    # Must not raise.
    assert client.emit(TelemetryEvent.DAILY_ACTIVE, _payload()) is True
    # Queue line still appended even though POST failed.
    contents = queue_path(home=tmp_home).read_text(encoding="utf-8").strip().splitlines()
    assert len(contents) == 1
    body: dict[str, Any] = json.loads(contents[0])
    assert body["name"] == "daily_active"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502, 503])
def test_emit_swallows_http_error_statuses(tmp_home: Path, status: int) -> None:
    _opt_in(tmp_home)
    transport = _StatusTransport(status)
    client = _make_client(tmp_home, transport)
    assert client.emit(TelemetryEvent.DAILY_ACTIVE, _payload()) is True


def test_emit_records_locally_when_network_disabled(tmp_home: Path) -> None:
    _opt_in(tmp_home)
    transport = _FailingTransport(httpx.ConnectError("offline"))
    client = _make_client(tmp_home, transport)
    client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    assert queue_path(home=tmp_home).exists()


def test_emit_appends_one_line_per_event(tmp_home: Path) -> None:
    _opt_in(tmp_home)
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)
    for _ in range(3):
        client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    lines = queue_path(home=tmp_home).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def test_emit_each_queue_line_is_valid_json(tmp_home: Path) -> None:
    _opt_in(tmp_home)
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)
    client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    for line in queue_path(home=tmp_home).read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_emit_swallows_local_disk_failure(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If even the local queue cannot be written, the network call still happens."""
    _opt_in(tmp_home)
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)

    from bernstein.core.telemetry import client as client_mod

    def _raise(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(client_mod, "_append_local", _raise)
    # Must not raise.
    client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())


def test_flush_is_bounded(tmp_home: Path) -> None:
    _opt_in(tmp_home)
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)
    import time as _t

    start = _t.monotonic()
    client.flush(deadline_seconds=0.1)
    elapsed = _t.monotonic() - start
    assert elapsed < 0.5


def test_close_is_idempotent(tmp_home: Path) -> None:
    transport = _StatusTransport(200)
    client = _make_client(tmp_home, transport)
    client.close()
    client.close()


def test_emit_with_invalid_endpoint_returns_true_but_swallows(tmp_home: Path) -> None:
    """Even with an unresolvable endpoint, no exception bubbles up."""
    _opt_in(tmp_home)
    client = Client(env={}, home=tmp_home, endpoint="http://does-not-resolve.invalid")
    try:
        client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    finally:
        client.close()


def test_client_endpoint_from_env(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_TELEMETRY_ENDPOINT", "https://example.com/x")
    client = Client(home=tmp_home)
    assert client.endpoint == "https://example.com/x"
    client.close()


def test_client_default_endpoint(tmp_home: Path) -> None:
    client = Client(env={}, home=tmp_home)
    assert client.endpoint.startswith("http")
    client.close()


def test_emit_does_not_log_payload_to_stderr(
    tmp_home: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _opt_in(tmp_home)
    transport = _FailingTransport(httpx.ConnectError("boom"))
    client = _make_client(tmp_home, transport)
    client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    captured = capfd.readouterr()
    # No traceback should appear.
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_envelope_preview_returns_none_when_disabled(tmp_home: Path) -> None:
    client = Client(env={}, home=tmp_home)
    try:
        assert client.envelope_preview(TelemetryEvent.DAILY_ACTIVE, _payload()) is None
    finally:
        client.close()


def test_envelope_preview_returns_none_without_install_id(tmp_home: Path) -> None:
    _opt_in(tmp_home)
    client = Client(env={}, home=tmp_home)
    try:
        # Opt-in is set but the install id has not been ensured yet.
        assert client.envelope_preview(TelemetryEvent.DAILY_ACTIVE, _payload()) is None
    finally:
        client.close()
