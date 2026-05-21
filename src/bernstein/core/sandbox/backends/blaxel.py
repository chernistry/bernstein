"""Blaxel cloud sandbox backend (optional extra).

Blaxel (https://blaxel.ai) provisions ephemeral execution sandboxes for
agent workloads through a REST API. This backend speaks that API
directly via :mod:`httpx` so the integration ships without pulling in a
provider SDK; the backend stays usable from a minimal install.

Environment variables
---------------------

The backend reads the following variables on construction:

- ``BLAXEL_API_KEY`` - required. Bearer token issued by the Blaxel
  control plane (Workspace Settings -> API Keys).
- ``BLAXEL_WORKSPACE`` - required. Workspace slug that owns the
  sandboxes.
- ``BLAXEL_API_URL`` - optional override for the API root. Defaults to
  ``https://api.blaxel.ai/v0``.

Capabilities
------------

Blaxel exposes file read/write, command exec with stdout+stderr capture,
outbound network, and persistent volumes that survive a session. It does
not expose a snapshot primitive in the public API, so this backend
declares :class:`SandboxCapability` accordingly.

Honest limitations
------------------

The current Blaxel public REST API does not stream exec output -
the backend polls the exec endpoint until the command finishes, which
caps end-to-end latency at the provider's poll interval rather than the
caller's wall clock. For interactive workloads (tail-style log
streaming) consider the worktree or Docker backends until Blaxel ships
WebSocket exec.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from base64 import b64decode, b64encode
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

_DEFAULT_API_URL = "https://api.blaxel.ai/v0"
_DEFAULT_RUNTIME = "python:3.13"


class BlaxelSandboxSession(SandboxSession):
    """Session backed by a Blaxel sandbox provisioned via REST."""

    backend_name = "blaxel"

    def __init__(
        self,
        *,
        session_id: str,
        sandbox_id: str,
        workspace: str,
        client: httpx.AsyncClient,
        workdir: str,
        base_env: Mapping[str, str],
        default_timeout: int,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir
        self._sandbox_id = sandbox_id
        self._workspace = workspace
        self._client = client
        self._base_env = dict(base_env)
        self._default_timeout = default_timeout
        self._closed = False

    async def read(self, path: str) -> bytes:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.get(
            f"/workspaces/{self._workspace}/sandboxes/{self._sandbox_id}/files",
            params={"path": resolved},
        )
        raise_for_status("blaxel", response)
        payload = response.json()
        if isinstance(payload, dict) and "content" in payload:
            content = payload["content"]
            if payload.get("encoding") == "base64":
                return b64decode(content)
            if isinstance(content, str):
                return content.encode("utf-8")
        # Fall back to raw bytes if the API ever switches to octet-stream.
        return response.content

    async def write(self, path: str, data: bytes, *, mode: int = 0o644) -> None:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.put(
            f"/workspaces/{self._workspace}/sandboxes/{self._sandbox_id}/files",
            json={
                "path": resolved,
                "content": b64encode(data).decode("ascii"),
                "encoding": "base64",
                "mode": mode,
            },
        )
        raise_for_status("blaxel", response)

    async def ls(self, path: str) -> list[str]:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.get(
            f"/workspaces/{self._workspace}/sandboxes/{self._sandbox_id}/files",
            params={"path": resolved, "list": "true"},
        )
        raise_for_status("blaxel", response)
        payload = response.json()
        entries: list[str] = []
        raw = payload.get("entries", []) if isinstance(payload, dict) else payload
        if isinstance(raw, list):
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
        effective_cwd = cwd if cwd is not None else self.workdir
        effective_timeout = timeout if timeout is not None else self._default_timeout
        merged_env = merge_exec_env(self._base_env, env)
        body: dict[str, Any] = {
            "argv": cmd,
            "cwd": effective_cwd,
            "env": merged_env,
            "timeout_seconds": effective_timeout,
        }
        if stdin is not None:
            body["stdin_b64"] = b64encode(stdin).decode("ascii")

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    f"/workspaces/{self._workspace}/sandboxes/{self._sandbox_id}/exec",
                    json=body,
                    timeout=float(effective_timeout) + 5.0,
                ),
                timeout=effective_timeout + 10,
            )
        except TimeoutError:
            raise TimeoutError(f"Command {cmd!r} timed out after {effective_timeout}s") from None
        raise_for_status("blaxel", response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected blaxel exec payload: {payload!r}")
        return ExecResult(
            exit_code=int(payload.get("exit_code", 0) or 0),
            stdout=_decode_stream(payload.get("stdout"), payload.get("stdout_encoding")),
            stderr=_decode_stream(payload.get("stderr"), payload.get("stderr_encoding")),
            duration_seconds=time.monotonic() - start,
        )

    async def snapshot(self) -> str:
        raise NotImplementedError(
            "Blaxel public REST API does not expose a snapshot primitive; "
            "track in vendor changelog before claiming SNAPSHOT capability.",
        )

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            response = await self._client.delete(
                f"/workspaces/{self._workspace}/sandboxes/{self._sandbox_id}",
            )
            if response.status_code not in (200, 202, 204, 404):
                raise_for_status("blaxel", response)
        except Exception as exc:
            logger.debug("Blaxel sandbox %s teardown raised: %s", self._sandbox_id, exc)
        finally:
            await self._client.aclose()


def _decode_stream(value: Any, encoding: Any) -> bytes:
    """Decode an exec stream payload as bytes (base64 or utf-8 string)."""
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        if encoding == "base64":
            return b64decode(value)
        return value.encode("utf-8")
    return str(value).encode("utf-8")


class BlaxelSandboxBackend:
    """Cloud :class:`SandboxBackend` powered by the Blaxel REST API."""

    name = "blaxel"
    capabilities: frozenset[SandboxCapability] = frozenset(
        {
            SandboxCapability.FILE_RW,
            SandboxCapability.EXEC,
            SandboxCapability.NETWORK,
            SandboxCapability.PERSISTENT_VOLUMES,
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
            client_factory: Optional callable used by tests to inject a
                pre-configured :class:`httpx.AsyncClient`. The factory
                receives ``(spec)`` and must return an
                :class:`httpx.AsyncClient`.
            tls: Optional mTLS configuration for connections that ride
                an internal CA (corporate VPC deployments). When absent
                the public PKI is used.
        """
        self._client_factory = client_factory
        self._tls = tls
        self._sessions: dict[str, BlaxelSandboxSession] = {}

    async def create(
        self,
        manifest: WorkspaceManifest,
        options: dict[str, Any] | None = None,
    ) -> SandboxSession:
        """Provision a new Blaxel sandbox and return a session.

        Recognised ``options``:

        - ``runtime``: Blaxel runtime image. Default ``python:3.13``.
        - ``region``: Optional region hint forwarded to the API.
        - ``session_id``: Explicit session identifier.
        """
        opts = dict(options or {})
        env = require_env("blaxel", ("BLAXEL_API_KEY", "BLAXEL_WORKSPACE"))
        api_url = opts.get("api_url") or os.environ.get("BLAXEL_API_URL") or _DEFAULT_API_URL
        workspace = env["BLAXEL_WORKSPACE"]
        session_id = allocate_session_id("bernstein-blaxel", opts.get("session_id"))
        spec = HttpClientSpec(
            base_url=str(api_url).rstrip("/"),
            headers={
                "Authorization": f"Bearer {env['BLAXEL_API_KEY']}",
                "Accept": "application/json",
                "User-Agent": "bernstein-sandbox/1.0",
            },
            timeout=float(manifest.timeout_seconds + 30),
            tls=self._tls,
        )
        client = self._client_factory(spec=spec) if self._client_factory is not None else build_async_client(spec)
        try:
            response = await client.post(
                f"/workspaces/{workspace}/sandboxes",
                json={
                    "runtime": opts.get("runtime", _DEFAULT_RUNTIME),
                    "region": opts.get("region"),
                    "workdir": manifest.root,
                    "env": dict(manifest.env),
                    "timeout_seconds": manifest.timeout_seconds,
                    "name": session_id,
                },
            )
        except Exception:
            await client.aclose()
            raise
        raise_for_status("blaxel", response)
        payload = response.json()
        sandbox_id = (payload.get("id") if isinstance(payload, dict) else None) or session_id
        session = BlaxelSandboxSession(
            session_id=session_id,
            sandbox_id=str(sandbox_id),
            workspace=workspace,
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
        raise NotImplementedError(
            "Blaxel backend does not declare SNAPSHOT capability; use create() against a persistent volume instead.",
        )

    async def destroy(self, session: SandboxSession) -> None:
        await session.shutdown()
        self._sessions.pop(session.session_id, None)


__all__ = [
    "BlaxelSandboxBackend",
    "BlaxelSandboxSession",
]
