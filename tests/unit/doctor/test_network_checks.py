"""Unit tests for network reachability doctor checks."""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from bernstein.cli.doctor import network_checks
from bernstein.cli.doctor.network_checks import (
    OFFLINE_ENV_VAR,
    PROVIDER_HOSTS,
    check_provider_reachability,
    run_network_checks,
)


def _run(coro: object) -> Any:
    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Offline switch
# ---------------------------------------------------------------------------


def test_check_skips_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OFFLINE_ENV_VAR, "1")
    result = _run(check_provider_reachability("anthropic"))
    assert result.status == "skip"
    assert result.detail == f"{OFFLINE_ENV_VAR}=1"


def test_run_network_checks_compact_skip_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OFFLINE_ENV_VAR, "1")
    results = _run(run_network_checks())
    assert len(results) == 1
    assert results[0].status == "skip"
    assert results[0].name == "network:*"


def test_offline_only_when_value_is_literal_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OFFLINE_ENV_VAR, "true")
    # Should not be treated as offline; provider lookup proceeds even for unknown providers.
    result = _run(check_provider_reachability("unknown-provider"))
    assert result.status == "skip"
    assert "unknown provider" in result.detail


# ---------------------------------------------------------------------------
# Unknown provider
# ---------------------------------------------------------------------------


def test_unknown_provider_returns_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)
    result = _run(check_provider_reachability("does-not-exist"))
    assert result.status == "skip"
    assert "unknown provider" in result.detail


# ---------------------------------------------------------------------------
# Reachability success / failure paths
# ---------------------------------------------------------------------------


def test_ok_path_uses_fake_open_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    async def fake_open(host: str, port: int) -> tuple[Any, Any]:
        class _W:
            def close(self) -> None: ...

        return (object(), _W())

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    result = _run(check_provider_reachability("anthropic"))
    assert result.status == "ok"
    assert "api.anthropic.com" in result.detail


def test_dns_failure_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    async def fake_open(host: str, port: int) -> Any:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    result = _run(check_provider_reachability("anthropic"))
    assert result.status == "fail"
    assert "DNS lookup failed" in result.detail
    assert "/etc/resolv.conf" in result.remediation


def test_connection_refused_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    async def fake_open(host: str, port: int) -> Any:
        raise OSError(111, "Connection refused")

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    result = _run(check_provider_reachability("openai"))
    assert result.status == "fail"
    assert "refused" in result.detail


def test_timeout_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    async def fake_open(host: str, port: int) -> Any:
        await asyncio.sleep(10)

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    result = _run(check_provider_reachability("openai", timeout=0.05))
    assert result.status == "fail"
    assert "timed out" in result.detail


# ---------------------------------------------------------------------------
# run_network_checks orchestration
# ---------------------------------------------------------------------------


def test_run_network_checks_uses_explicit_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    async def fake_open(host: str, port: int) -> tuple[Any, Any]:
        class _W:
            def close(self) -> None: ...

        return (object(), _W())

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    results = _run(run_network_checks(["anthropic", "openai"]))
    assert {r.name for r in results} == {"network:anthropic", "network:openai"}


def test_run_network_checks_default_covers_all_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    async def fake_open(host: str, port: int) -> Any:
        raise OSError(111, "refused")

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    results = _run(run_network_checks())
    assert {r.name for r in results} == {f"network:{p}" for p in PROVIDER_HOSTS}


def test_run_network_checks_empty_providers_returns_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)
    results = _run(run_network_checks([], hosts={}))
    assert len(results) == 1
    assert results[0].name == "network:none"


def test_provider_hosts_table_is_complete() -> None:
    for required in ("anthropic", "openai", "google", "openrouter"):
        assert required in PROVIDER_HOSTS, f"missing provider {required}"
        assert PROVIDER_HOSTS[required], f"empty host for {required}"


def test_custom_host_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OFFLINE_ENV_VAR, raising=False)

    captured: dict[str, Any] = {}

    async def fake_open(host: str, port: int) -> tuple[Any, Any]:
        captured["host"] = host
        captured["port"] = port

        class _W:
            def close(self) -> None: ...

        return (object(), _W())

    monkeypatch.setattr(network_checks.asyncio, "open_connection", fake_open)

    result = _run(check_provider_reachability("anthropic", host="proxy.example.test", port=8443))
    assert result.status == "ok"
    assert captured == {"host": "proxy.example.test", "port": 8443}
