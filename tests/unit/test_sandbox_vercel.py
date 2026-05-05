"""Unit tests for :mod:`bernstein.core.sandbox.backends.vercel`."""

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
from bernstein.core.sandbox.backends.vercel import VercelSandboxBackend

API_URL = "https://api.vercel.example"


def _set_env(monkeypatch: pytest.MonkeyPatch, *, team_id: str | None = None) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "tok-test")
    monkeypatch.setenv("VERCEL_API_URL", API_URL)
    if team_id is not None:
        monkeypatch.setenv("VERCEL_TEAM_ID", team_id)
    else:
        monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)


def test_capabilities_shape() -> None:
    backend = VercelSandboxBackend()
    assert SandboxCapability.FILE_RW in backend.capabilities
    assert SandboxCapability.EXEC in backend.capabilities
    assert SandboxCapability.NETWORK in backend.capabilities
    assert SandboxCapability.SNAPSHOT not in backend.capabilities


@pytest.mark.asyncio
async def test_create_missing_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    backend = VercelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with pytest.raises(SandboxCredentialError) as exc:
        await backend.create(manifest)
    assert "VERCEL_TOKEN" in str(exc.value)


@pytest.mark.asyncio
async def test_spawn_and_exec_happy_path_with_team(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, team_id="team-abc")
    backend = VercelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=30)

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        create = mock.post("/v1/sandboxes").mock(
            return_value=httpx.Response(201, json={"id": "sbx-50"}),
        )
        exec_route = mock.post("/v1/sandboxes/sbx-50/exec").mock(
            return_value=httpx.Response(
                200,
                json={
                    "exitCode": 0,
                    "stdout": "ready\n",
                    "stderr": "",
                },
            ),
        )
        mock.delete("/v1/sandboxes/sbx-50").mock(return_value=httpx.Response(204))

        session = await backend.create(manifest)
        try:
            result = await session.exec(["echo", "ready"])
            assert result.exit_code == 0
            assert b"ready" in result.stdout
            assert exec_route.called
            # team scope must propagate
            assert "teamId=team-abc" in str(create.calls.last.request.url)
        finally:
            await backend.destroy(session)


@pytest.mark.asyncio
async def test_kill_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = VercelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/v1/sandboxes").mock(return_value=httpx.Response(201, json={"id": "sbx-1"}))
        mock.delete("/v1/sandboxes/sbx-1").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        await session.shutdown()
        await session.shutdown()


@pytest.mark.asyncio
async def test_create_propagates_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = VercelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/v1/sandboxes").mock(
            return_value=httpx.Response(
                402,
                json={"error": "payment required"},
                headers={"X-Vercel-Id": "req-402"},
            ),
        )
        with pytest.raises(SandboxApiError) as exc:
            await backend.create(manifest)
        assert exc.value.status_code == 402
        assert exc.value.request_id == "req-402"


@pytest.mark.asyncio
async def test_exec_propagates_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = VercelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/v1/sandboxes").mock(return_value=httpx.Response(201, json={"id": "sbx-77"}))
        mock.post("/v1/sandboxes/sbx-77/exec").mock(
            return_value=httpx.Response(
                504,
                json={"error": "gateway"},
                headers={"X-Vercel-Id": "req-504"},
            ),
        )
        mock.delete("/v1/sandboxes/sbx-77").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        try:
            with pytest.raises(SandboxApiError) as exc:
                await session.exec(["false"])
            assert exc.value.status_code == 504
            assert exc.value.request_id == "req-504"
        finally:
            await backend.destroy(session)


@pytest.mark.asyncio
async def test_snapshot_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = VercelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/v1/sandboxes").mock(return_value=httpx.Response(201, json={"id": "sbx-x"}))
        mock.delete("/v1/sandboxes/sbx-x").mock(return_value=httpx.Response(204))
        session = await backend.create(manifest)
        try:
            with pytest.raises(NotImplementedError):
                await session.snapshot()
        finally:
            await backend.destroy(session)


def test_registry_lists_vercel() -> None:
    from bernstein.core.sandbox import list_backend_names

    assert "vercel" in list_backend_names()
