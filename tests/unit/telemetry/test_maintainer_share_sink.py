"""Maintainer-share telemetry sink tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from bernstein.core.lineage.identity import AgentCard, verify_detached
from bernstein.core.telemetry import consent, write_enabled
from bernstein.core.telemetry.client import Client
from bernstein.core.telemetry.events import DailyActivePayload, TelemetryEvent
from bernstein.core.telemetry.share import (
    SHARE_ENDPOINT_ENV,
    share_private_key_path,
)


class _RecordingTransport(httpx.MockTransport):
    """HTTP transport that records every request body and header set."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(204, content=b"")


def _payload() -> DailyActivePayload:
    return DailyActivePayload(day_iso="2026-05-22")


def _test_env(tmp_home: Path, overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = {"XDG_CONFIG_HOME": str(tmp_home / ".config")}
    if overrides is not None:
        env.update(overrides)
    return env


def _emit_with(
    tmp_home: Path,
    transport: _RecordingTransport,
    *,
    env: dict[str, str],
) -> bool:
    write_enabled(True, home=tmp_home)
    http = httpx.Client(transport=transport)
    client = Client(
        env=env,
        home=tmp_home,
        endpoint="https://operator.example.test/v1/events",
        http_client=http,
    )
    try:
        return client.emit(TelemetryEvent.DAILY_ACTIVE, _payload())
    finally:
        client.close()


def test_share_flag_and_endpoint_send_same_event_with_detached_receipt(tmp_home: Path) -> None:
    """Explicit share consent plus endpoint emits a signed copy of the event."""
    env = _test_env(tmp_home, {SHARE_ENDPOINT_ENV: "https://maintainer.example.test/v1/events"})
    consent.write_share_flag(True, env=env, home=tmp_home)
    transport = _RecordingTransport()

    assert _emit_with(
        tmp_home,
        transport,
        env=env,
    )

    assert [request.url.host for request in transport.requests] == [
        "operator.example.test",
        "maintainer.example.test",
    ]
    operator_request, share_request = transport.requests
    assert share_request.content == operator_request.content

    body = json.loads(share_request.content)
    assert set(body) == {"install_id", "name", "payload", "schema_version", "timestamp"}
    assert body["name"] == TelemetryEvent.DAILY_ACTIVE.value

    jws = share_request.headers["x-bernstein-telemetry-jws"]
    kid = share_request.headers["x-bernstein-telemetry-kid"]
    public_key_pem = base64.urlsafe_b64decode(
        share_request.headers["x-bernstein-telemetry-public-key-pem-b64"] + "=="
    ).decode("ascii")
    card = AgentCard(
        agent_id=share_request.headers["x-bernstein-telemetry-agent-id"],
        kid=kid,
        public_key_pem=public_key_pem,
    )
    assert verify_detached(share_request.content, jws, card)


def test_share_endpoint_without_share_consent_does_not_send_or_create_key(tmp_home: Path) -> None:
    """Endpoint configuration alone is not consent."""
    env = _test_env(tmp_home, {SHARE_ENDPOINT_ENV: "https://maintainer.example.test/v1/events"})
    transport = _RecordingTransport()

    assert _emit_with(
        tmp_home,
        transport,
        env=env,
    )

    assert [request.url.host for request in transport.requests] == ["operator.example.test"]
    assert not share_private_key_path(tmp_home).exists()


def test_share_consent_without_endpoint_does_not_send_or_create_key(tmp_home: Path) -> None:
    """Consent alone is inert until the endpoint is configured out of package."""
    env = _test_env(tmp_home)
    consent.write_share_flag(True, env=env, home=tmp_home)
    transport = _RecordingTransport()

    assert _emit_with(tmp_home, transport, env=env)

    assert [request.url.host for request in transport.requests] == ["operator.example.test"]
    assert not share_private_key_path(tmp_home).exists()


def test_share_consent_with_non_https_endpoint_does_not_send_or_create_key(tmp_home: Path) -> None:
    """The maintainer-share endpoint must be supplied as HTTPS."""
    env = _test_env(tmp_home, {SHARE_ENDPOINT_ENV: "http://maintainer.example.test/v1/events"})
    consent.write_share_flag(True, env=env, home=tmp_home)
    transport = _RecordingTransport()

    assert _emit_with(
        tmp_home,
        transport,
        env=env,
    )

    assert [request.url.host for request in transport.requests] == ["operator.example.test"]
    assert not share_private_key_path(tmp_home).exists()


def test_share_consent_does_not_send_when_local_audit_append_fails(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The share sink requires the local audit queue copy to exist first."""
    env = _test_env(tmp_home, {SHARE_ENDPOINT_ENV: "https://maintainer.example.test/v1/events"})
    consent.write_share_flag(True, env=env, home=tmp_home)
    transport = _RecordingTransport()

    from bernstein.core.telemetry import client as client_mod

    def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(client_mod, "_append_local", _raise)

    assert _emit_with(
        tmp_home,
        transport,
        env=env,
    )

    assert [request.url.host for request in transport.requests] == ["operator.example.test"]
    assert not share_private_key_path(tmp_home).exists()
