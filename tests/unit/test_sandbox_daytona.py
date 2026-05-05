"""Unit tests for :mod:`bernstein.core.sandbox.backends.daytona`."""

from __future__ import annotations

import httpx
import pytest
import respx

from bernstein.core.sandbox import (
    SandboxCapability,
    WorkspaceManifest,
)
from bernstein.core.sandbox.backends._http_helpers import (
    SandboxApiError,
    SandboxCredentialError,
)
from bernstein.core.sandbox.backends.daytona import DaytonaSandboxBackend

API_URL = "https://app.daytona.example/api"


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAYTONA_API_KEY", "tok-test")
    monkeypatch.setenv("DAYTONA_API_URL", API_URL)


def test_capabilities_shape() -> None:
    backend = DaytonaSandboxBackend()
    assert SandboxCapability.FILE_RW in backend.capabilities
    assert SandboxCapability.EXEC in backend.capabilities
    assert SandboxCapability.NETWORK in backend.capabilities
    assert SandboxCapability.SNAPSHOT in backend.capabilities


@pytest.mark.asyncio
async def test_create_missing_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    backend = DaytonaSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with pytest.raises(SandboxCredentialError) as exc:
        await backend.create(manifest)
    assert "DAYTONA_API_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_spawn_and_exec_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = DaytonaSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=30)

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/sandbox").mock(return_value=httpx.Response(201, json={"id": "sbx-77"}))
        exec_route = mock.post(
            "/sandbox/sbx-77/toolbox/process/execute",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "exit_code": 0,
                    "stdout": "ok\n",
                    "stderr": "",
                },
            ),
        )
        mock.delete("/sandbox/sbx-77").mock(return_value=httpx.Response(204))

        session = await backend.create(manifest)
        try:
            result = await session.exec(["echo", "ok"])
            assert result.exit_code == 0
            assert b"ok" in result.stdout
            assert exec_route.called
        finally:
            await backend.destroy(session)


@pytest.mark.asyncio
async def test_kill_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = DaytonaSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/sandbox").mock(return_value=httpx.Response(201, json={"id": "sbx-1"}))
        mock.delete("/sandbox/sbx-1").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        await session.shutdown()
        await session.shutdown()


@pytest.mark.asyncio
async def test_create_propagates_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = DaytonaSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/sandbox").mock(
            return_value=httpx.Response(
                401,
                json={"error": "unauthorised"},
                headers={"X-Request-Id": "req-401"},
            ),
        )
        with pytest.raises(SandboxApiError) as exc:
            await backend.create(manifest)
        assert exc.value.status_code == 401
        assert exc.value.request_id == "req-401"


@pytest.mark.asyncio
async def test_exec_propagates_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = DaytonaSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/sandbox").mock(return_value=httpx.Response(201, json={"id": "sbx-9"}))
        mock.post("/sandbox/sbx-9/toolbox/process/execute").mock(
            return_value=httpx.Response(503, json={"error": "boom"}, headers={"X-Request-Id": "req-503"}),
        )
        mock.delete("/sandbox/sbx-9").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        try:
            with pytest.raises(SandboxApiError) as exc:
                await session.exec(["false"])
            assert exc.value.status_code == 503
            assert exc.value.request_id == "req-503"
        finally:
            await backend.destroy(session)


def test_registry_lists_daytona() -> None:
    from bernstein.core.sandbox import list_backend_names

    assert "daytona" in list_backend_names()
