"""Unit tests for :mod:`bernstein.core.sandbox.backends.blaxel`.

The tests mock the HTTP layer with respx and exercise:

- spawn happy path
- exec on session
- shutdown / kill
- missing-creds error path
- 4xx error propagation including request id

Live integration tests live under ``tests/integration/sandbox/`` and
are gated by ``CI_BLAXEL_TEST``.
"""

from __future__ import annotations

from base64 import b64encode

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
from bernstein.core.sandbox.backends.blaxel import BlaxelSandboxBackend

API_URL = "https://api.blaxel.example/v0"


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLAXEL_API_KEY", "tok-test")
    monkeypatch.setenv("BLAXEL_WORKSPACE", "ws-bernstein")
    monkeypatch.setenv("BLAXEL_API_URL", API_URL)


def test_capabilities_shape() -> None:
    backend = BlaxelSandboxBackend()
    assert SandboxCapability.FILE_RW in backend.capabilities
    assert SandboxCapability.EXEC in backend.capabilities
    assert SandboxCapability.NETWORK in backend.capabilities
    assert SandboxCapability.PERSISTENT_VOLUMES in backend.capabilities
    assert SandboxCapability.SNAPSHOT not in backend.capabilities


@pytest.mark.asyncio
async def test_create_missing_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLAXEL_API_KEY", raising=False)
    monkeypatch.delenv("BLAXEL_WORKSPACE", raising=False)
    backend = BlaxelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")
    with pytest.raises(SandboxCredentialError) as exc:
        await backend.create(manifest)
    assert "BLAXEL_API_KEY" in str(exc.value)
    assert "BLAXEL_WORKSPACE" in str(exc.value)


@pytest.mark.asyncio
async def test_spawn_and_exec_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = BlaxelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=30)

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/workspaces/ws-bernstein/sandboxes").mock(
            return_value=httpx.Response(201, json={"id": "sbx-42"}),
        )
        exec_route = mock.post(
            "/workspaces/ws-bernstein/sandboxes/sbx-42/exec",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "exit_code": 0,
                    "stdout": b64encode(b"hi\n").decode("ascii"),
                    "stdout_encoding": "base64",
                    "stderr": "",
                    "stderr_encoding": "utf-8",
                },
            ),
        )
        mock.delete("/workspaces/ws-bernstein/sandboxes/sbx-42").mock(
            return_value=httpx.Response(204),
        )

        session = await backend.create(manifest)
        try:
            result = await session.exec(["echo", "hi"])
            assert result.exit_code == 0
            assert result.stdout == b"hi\n"
            assert result.stderr == b""
            assert exec_route.called
        finally:
            await backend.destroy(session)


@pytest.mark.asyncio
async def test_kill_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = BlaxelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/workspaces/ws-bernstein/sandboxes").mock(
            return_value=httpx.Response(201, json={"id": "sbx-99"}),
        )
        mock.delete("/workspaces/ws-bernstein/sandboxes/sbx-99").mock(
            return_value=httpx.Response(204),
        )

        session = await backend.create(manifest)
        await session.shutdown()
        await session.shutdown()  # must not raise


@pytest.mark.asyncio
async def test_create_propagates_4xx_with_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch)
    backend = BlaxelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/workspaces/ws-bernstein/sandboxes").mock(
            return_value=httpx.Response(
                403,
                json={"error": "forbidden"},
                headers={"X-Request-Id": "req-bad-1"},
            ),
        )
        with pytest.raises(SandboxApiError) as exc:
            await backend.create(manifest)
        assert exc.value.status_code == 403
        assert exc.value.request_id == "req-bad-1"


@pytest.mark.asyncio
async def test_exec_propagates_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch)
    backend = BlaxelSandboxBackend()
    manifest = WorkspaceManifest(root="/workspace")

    with respx.mock(base_url=API_URL, assert_all_called=False) as mock:
        mock.post("/workspaces/ws-bernstein/sandboxes").mock(
            return_value=httpx.Response(201, json={"id": "sbx-7"}),
        )
        mock.post("/workspaces/ws-bernstein/sandboxes/sbx-7/exec").mock(
            return_value=httpx.Response(
                500,
                json={"error": "boom"},
                headers={"X-Request-Id": "req-500"},
            ),
        )
        mock.delete("/workspaces/ws-bernstein/sandboxes/sbx-7").mock(
            return_value=httpx.Response(204),
        )

        session = await backend.create(manifest)
        try:
            with pytest.raises(SandboxApiError) as exc:
                await session.exec(["false"])
            assert exc.value.status_code == 500
            assert exc.value.request_id == "req-500"
        finally:
            await backend.destroy(session)


def test_registry_lists_blaxel() -> None:
    from bernstein.core.sandbox import list_backend_names

    names = list_backend_names()
    assert "blaxel" in names
