"""Daytona cloud sandbox backend (optional extra).

Daytona (https://daytona.io) provisions cloud development environments
as ephemeral sandboxes. This backend speaks the public REST API
directly via :mod:`httpx`; the optional :mod:`daytona-sdk` Python SDK
is *not* required and is not pulled in by default.

Environment variables
---------------------

The backend reads the following variables on construction:

- ``DAYTONA_API_KEY`` — required. Personal access token (Daytona
  Dashboard -> Settings -> API Keys).
- ``DAYTONA_API_URL`` — optional override of the API root. Defaults to
  ``https://app.daytona.io/api``.
- ``DAYTONA_TARGET`` — optional region/target identifier (e.g.
  ``us``, ``eu``).
- ``DAYTONA_ORG_ID`` — optional organisation identifier sent as the
  ``X-Daytona-Organization-ID`` header for multi-org accounts.

Capabilities
------------

`FILE_RW`, `EXEC`, `NETWORK`, `SNAPSHOT`. Daytona supports snapshotting
sandboxes via its public API; we expose this through
:class:`SandboxCapability.SNAPSHOT`.

Honest limitations
------------------

- The REST exec endpoint returns the final stdout/stderr after the
  command exits. For exec-streaming use the Daytona WebSocket exec
  channel; that is tracked as a follow-up because Bernstein's
  :class:`SandboxSession` protocol is currently unary-response.
- Stdin injection is unsupported by the REST exec endpoint; the
  backend raises :class:`NotImplementedError` when ``stdin`` is set.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from base64 import b64decode
from typing import TYPE_CHECKING, Any

from bernstein.core.sandbox.backend import (
    ExecResult,
    SandboxCapability,
    SandboxSession,
)
from bernstein.core.sandbox.backends._http_helpers import (
    HttpClientSpec,
    build_async_client,
    raise_for_status,
    require_env,
)
from bernstein.core.sandbox.backends._remote_helpers import (
    allocate_session_id,
    guard_exec_preconditions,
    merge_exec_env,
    resolve_posix_path,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from bernstein.core.protocols.cluster.cluster_tls import TLSConfig
    from bernstein.core.sandbox.manifest import WorkspaceManifest

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://app.daytona.io/api"
_DEFAULT_IMAGE = "daytonaio/sandbox:latest"


class DaytonaSandboxSession(SandboxSession):
    """Session backed by a Daytona sandbox provisioned via REST."""

    backend_name = "daytona"

    def __init__(
        self,
        *,
        session_id: str,
        sandbox_id: str,
        client: httpx.AsyncClient,
        workdir: str,
        base_env: Mapping[str, str],
        default_timeout: int,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir
        self._sandbox_id = sandbox_id
        self._client = client
        self._base_env = dict(base_env)
        self._default_timeout = default_timeout
        self._closed = False

    async def read(self, path: str) -> bytes:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.get(
            f"/sandbox/{self._sandbox_id}/toolbox/files/download",
            params={"path": resolved},
        )
        raise_for_status("daytona", response)
        return response.content

    async def write(self, path: str, data: bytes, *, mode: int = 0o644) -> None:
        resolved = resolve_posix_path(self.workdir, path)
        files = {"file": (resolved.rsplit("/", 1)[-1], data, "application/octet-stream")}
        response = await self._client.post(
            f"/sandbox/{self._sandbox_id}/toolbox/files/upload",
            params={"path": resolved, "mode": oct(mode)},
            files=files,
        )
        raise_for_status("daytona", response)

    async def ls(self, path: str) -> list[str]:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.get(
            f"/sandbox/{self._sandbox_id}/toolbox/files",
            params={"path": resolved},
        )
        raise_for_status("daytona", response)
        payload = response.json()
        entries: list[str] = []
        if isinstance(payload, list):
            raw = payload
        elif isinstance(payload, dict) and isinstance(payload.get("files"), list):
            raw = payload["files"]
        else:
            raw = []
        for entry in raw:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("path")
                if isinstance(name, str):
                    entries.append(name.rsplit("/", 1)[-1])
            elif isinstance(entry, str):
                entries.append(entry.rsplit("/", 1)[-1])
        return sorted(entries)

    async def exec(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
        stdin: bytes | None = None,
    ) -> ExecResult:
        guard_exec_preconditions(self._closed, self.session_id, cmd)
        if stdin is not None:
            raise NotImplementedError(
                "Daytona REST exec endpoint does not accept stdin; use the "
                "WebSocket exec channel for interactive workloads.",
            )
        effective_cwd = cwd if cwd is not None else self.workdir
        effective_timeout = timeout if timeout is not None else self._default_timeout
        merged_env = merge_exec_env(self._base_env, env)

        body: dict[str, Any] = {
            "command": cmd,
            "cwd": effective_cwd,
            "env": merged_env,
            "timeout": effective_timeout,
        }
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    f"/sandbox/{self._sandbox_id}/toolbox/process/execute",
                    json=body,
                    timeout=float(effective_timeout) + 5.0,
                ),
                timeout=effective_timeout + 10,
            )
        except TimeoutError:
            raise TimeoutError(f"Command {cmd!r} timed out after {effective_timeout}s") from None
        raise_for_status("daytona", response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected daytona exec payload: {payload!r}")
        stdout_b = _decode_stream(payload.get("stdout"), payload.get("stdout_encoding"))
        stderr_b = _decode_stream(payload.get("stderr"), payload.get("stderr_encoding"))
        if not stdout_b and not stderr_b and "result" in payload:
            stdout_b = _decode_stream(payload.get("result"), None)
        return ExecResult(
            exit_code=int(payload.get("exit_code", payload.get("exitCode", 0)) or 0),
            stdout=stdout_b,
            stderr=stderr_b,
            duration_seconds=time.monotonic() - start,
        )

    async def snapshot(self) -> str:
        response = await self._client.post(
            f"/sandbox/{self._sandbox_id}/snapshots",
            json={"name": f"bernstein-{self.session_id}"},
        )
        raise_for_status("daytona", response)
        payload = response.json()
        if isinstance(payload, dict):
            snap_id = payload.get("id") or payload.get("snapshotId")
            if isinstance(snap_id, str):
                return snap_id
        raise RuntimeError(f"Daytona snapshot did not return an id: {payload!r}")

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            response = await self._client.delete(f"/sandbox/{self._sandbox_id}")
            if response.status_code not in (200, 202, 204, 404):
                raise_for_status("daytona", response)
        except Exception as exc:
            logger.debug("Daytona sandbox %s teardown raised: %s", self._sandbox_id, exc)
        finally:
            await self._client.aclose()


def _decode_stream(value: Any, encoding: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        if encoding == "base64":
            return b64decode(value)
        return value.encode("utf-8")
    return str(value).encode("utf-8")


def _build_headers(api_key: str, org_id: str | None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "bernstein-sandbox/1.0",
    }
    if org_id:
        headers["X-Daytona-Organization-ID"] = org_id
    return headers


class DaytonaSandboxBackend:
    """Cloud :class:`SandboxBackend` powered by the Daytona REST API."""

    name = "daytona"
    capabilities: frozenset[SandboxCapability] = frozenset(
        {
            SandboxCapability.FILE_RW,
            SandboxCapability.EXEC,
            SandboxCapability.NETWORK,
            SandboxCapability.SNAPSHOT,
        }
    )

    def __init__(
        self,
        *,
        client_factory: Any | None = None,
        tls: TLSConfig | None = None,
    ) -> None:
        """Create the backend.

        Args:
            client_factory: Optional callable returning an
                :class:`httpx.AsyncClient` for tests. Receives the
                resolved :class:`HttpClientSpec`.
            tls: Optional mTLS configuration for self-hosted Daytona
                control planes behind a private CA.
        """
        self._client_factory = client_factory
        self._tls = tls
        self._sessions: dict[str, DaytonaSandboxSession] = {}

    async def create(
        self,
        manifest: WorkspaceManifest,
        options: dict[str, Any] | None = None,
    ) -> SandboxSession:
        """Provision a new Daytona sandbox.

        Recognised ``options``:

        - ``image``: container image. Default ``daytonaio/sandbox:latest``.
        - ``target``: region/target hint. Default reads ``DAYTONA_TARGET``.
        - ``cpu``, ``memory``, ``disk``: resource requests forwarded to
          the API.
        - ``session_id``: explicit session identifier.
        """
        opts = dict(options or {})
        env = require_env("daytona", ("DAYTONA_API_KEY",))
        api_url = opts.get("api_url") or os.environ.get("DAYTONA_API_URL") or _DEFAULT_API_URL
        target = opts.get("target") or os.environ.get("DAYTONA_TARGET")
        org_id = opts.get("org_id") or os.environ.get("DAYTONA_ORG_ID")
        session_id = allocate_session_id("bernstein-daytona", opts.get("session_id"))
        spec = HttpClientSpec(
            base_url=str(api_url).rstrip("/"),
            headers=_build_headers(env["DAYTONA_API_KEY"], org_id),
            timeout=float(manifest.timeout_seconds + 30),
            tls=self._tls,
        )
        client = self._client_factory(spec=spec) if self._client_factory is not None else build_async_client(spec)
        body: dict[str, Any] = {
            "name": session_id,
            "image": opts.get("image", _DEFAULT_IMAGE),
            "user": opts.get("user"),
            "env": dict(manifest.env),
            "cwd": manifest.root,
        }
        if target:
            body["target"] = target
        for key in ("cpu", "memory", "disk", "gpu"):
            if key in opts:
                body[key] = opts[key]
        try:
            response = await client.post("/sandbox", json=body)
        except Exception:
            await client.aclose()
            raise
        raise_for_status("daytona", response)
        payload = response.json()
        sandbox_id = (payload.get("id") if isinstance(payload, dict) else None) or session_id
        session = DaytonaSandboxSession(
            session_id=session_id,
            sandbox_id=str(sandbox_id),
            client=client,
            workdir=manifest.root,
            base_env=manifest.env,
            default_timeout=manifest.timeout_seconds,
        )
        for entry in manifest.files:
            await session.write(entry.path, entry.content, mode=entry.mode)
        self._sessions[session_id] = session
        return session

    async def resume(self, snapshot_id: str) -> SandboxSession:
        """Restore a Daytona sandbox from a snapshot id."""
        env = require_env("daytona", ("DAYTONA_API_KEY",))
        api_url = os.environ.get("DAYTONA_API_URL") or _DEFAULT_API_URL
        org_id = os.environ.get("DAYTONA_ORG_ID")
        spec = HttpClientSpec(
            base_url=str(api_url).rstrip("/"),
            headers=_build_headers(env["DAYTONA_API_KEY"], org_id),
            timeout=120.0,
            tls=self._tls,
        )
        client = self._client_factory(spec=spec) if self._client_factory is not None else build_async_client(spec)
        try:
            response = await client.post(
                "/sandbox",
                json={"name": f"bernstein-resume-{snapshot_id}", "snapshot_id": snapshot_id},
            )
        except Exception:
            await client.aclose()
            raise
        raise_for_status("daytona", response)
        payload = response.json()
        sandbox_id = (payload.get("id") if isinstance(payload, dict) else None) or snapshot_id
        session = DaytonaSandboxSession(
            session_id=f"resume-{snapshot_id}",
            sandbox_id=str(sandbox_id),
            client=client,
            workdir="/workspace",
            base_env={},
            default_timeout=1800,
        )
        self._sessions[session.session_id] = session
        return session

    async def destroy(self, session: SandboxSession) -> None:
        await session.shutdown()
        self._sessions.pop(session.session_id, None)


__all__ = [
    "DaytonaSandboxBackend",
    "DaytonaSandboxSession",
]
