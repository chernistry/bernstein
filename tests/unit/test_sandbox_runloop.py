"""Unit tests for :mod:`bernstein.core.sandbox.backends.runloop`."""

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
from bernstein.core.sandbox.backends.runloop import RunloopSandboxBackend

API_URL = "https://api.runloop.example/v1"


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNLOOP_API_KEY", "tok-test")
    monkeypatch.setenv("RUNLOOP_API_URL", API_URL)


def test_capabilities_shape() -> None:
    backend = RunloopSandboxBackend()
    assert SandboxCapability.FILE_RW in backend.capabilities
    assert SandboxCapability.EXEC in backend.capabilities
    assert SandboxCapability.NETWORK in backend.capabilities
    assert SandboxCapability.SNAPSHOT in backend.capabilities


@pytest.mark.asyncio
async def test_create_missing_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNLOOP_API_KEY", raising=False)
    backend = RunloopSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with pytest.raises(SandboxCredentialError) as exc:
        await backend.create(manifest)
    assert "RUNLOOP_API_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_spawn_and_exec_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = RunloopSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=30)

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/devboxes").mock(return_value=httpx.Response(201, json={"id": "dvb-12"}))
        exec_route = mock.post(
            "/devboxes/dvb-12/execute_sync",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "exit_status": 0,
                    "stdout": "hello\n",
                    "stderr": "",
                },
            ),
        )
        mock.post("/devboxes/dvb-12/shutdown").mock(return_value=httpx.Response(204))

        session = await backend.create(manifest)
        try:
            result = await session.exec(["echo", "hello"])
            assert result.exit_code == 0
            assert b"hello" in result.stdout
            assert exec_route.called
        finally:
            await backend.destroy(session)


@pytest.mark.asyncio
async def test_kill_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = RunloopSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/devboxes").mock(return_value=httpx.Response(201, json={"id": "dvb-2"}))
        mock.post("/devboxes/dvb-2/shutdown").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        await session.shutdown()
        await session.shutdown()


@pytest.mark.asyncio
async def test_create_propagates_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = RunloopSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/devboxes").mock(
            return_value=httpx.Response(
                429,
                json={"error": "rate-limited"},
                headers={"X-Request-Id": "req-429"},
            ),
        )
        with pytest.raises(SandboxApiError) as exc:
            await backend.create(manifest)
        assert exc.value.status_code == 429
        assert exc.value.request_id == "req-429"


@pytest.mark.asyncio
async def test_exec_propagates_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = RunloopSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/devboxes").mock(return_value=httpx.Response(201, json={"id": "dvb-3"}))
        mock.post("/devboxes/dvb-3/execute_sync").mock(
            return_value=httpx.Response(502, json={"error": "boom"}, headers={"X-Request-Id": "req-502"}),
        )
        mock.post("/devboxes/dvb-3/shutdown").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        try:
            with pytest.raises(SandboxApiError) as exc:
                await session.exec(["false"])
            assert exc.value.status_code == 502
            assert exc.value.request_id == "req-502"
        finally:
            await backend.destroy(session)


@pytest.mark.asyncio
async def test_stdin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = RunloopSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/devboxes").mock(return_value=httpx.Response(201, json={"id": "dvb-9"}))
        mock.post("/devboxes/dvb-9/shutdown").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        try:
            with pytest.raises(NotImplementedError):
                await session.exec(["cat"], stdin=b"hello")
        finally:
            await backend.destroy(session)


def test_registry_lists_runloop() -> None:
    from bernstein.core.sandbox import list_backend_names

    assert "runloop" in list_backend_names()
