"""Vercel Sandbox cloud backend (optional extra).

Vercel Sandbox (https://vercel.com/docs/sandbox) provisions ephemeral
Firecracker microVMs intended for AI-agent workloads. This backend
speaks Vercel's REST API directly via :mod:`httpx`; no Vercel SDK is
required.

Environment variables
---------------------

- ``VERCEL_TOKEN`` - required. Personal/team API token from
  ``https://vercel.com/account/tokens``.
- ``VERCEL_TEAM_ID`` - optional team scope id; required for tokens
  scoped to a team.
- ``VERCEL_API_URL`` - optional override of the API root. Defaults to
  ``https://api.vercel.com``.

Capabilities
------------

`FILE_RW`, `EXEC`, `NETWORK`. Vercel Sandbox does not currently expose
a public snapshot/restore endpoint, so :class:`SandboxCapability.SNAPSHOT`
is not declared.

Honest limitations
------------------

- **No exec streaming on the synchronous endpoint.** The Vercel
  Sandbox HTTP API returns the buffered ``stdout``/``stderr`` after
  the command exits. For interactive log-tailing workloads use the
  ``worktree`` or ``docker`` backends until Vercel's WebSocket exec
  channel ships GA.
- **Stdin not supported on the sync exec route.** Setting ``stdin=``
  raises :class:`NotImplementedError`.
- **Snapshots not supported.** Persist state via Vercel-managed
  storage (e.g. Vercel KV/Blob) rather than relying on session
  snapshotting.
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

_DEFAULT_API_URL = "https://api.vercel.com"
_DEFAULT_RUNTIME = "node22"


class VercelSandboxSession(SandboxSession):
    """Session backed by a Vercel Sandbox provisioned via REST."""

    backend_name = "vercel"

    def __init__(
        self,
        *,
        session_id: str,
        sandbox_id: str,
        client: httpx.AsyncClient,
        team_query: dict[str, str],
        workdir: str,
        base_env: Mapping[str, str],
        default_timeout: int,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir
        self._sandbox_id = sandbox_id
        self._client = client
        self._team_query = team_query
        self._base_env = dict(base_env)
        self._default_timeout = default_timeout
        self._closed = False

    async def read(self, path: str) -> bytes:
        resolved = resolve_posix_path(self.workdir, path)
        params = {
            "path": resolved,
        } | self._team_query
        response = await self._client.get(
            f"/v1/sandboxes/{self._sandbox_id}/files",
            params=params,
        )
        raise_for_status("vercel", response)
        ctype = response.headers.get("content-type", "")
        if "json" in ctype:
            payload = response.json()
            if isinstance(payload, dict):
                content = payload.get("content")
                encoding = payload.get("encoding")
                if isinstance(content, str):
                    if encoding == "base64":
                        return b64decode(content)
                    return content.encode("utf-8")
        return response.content

    async def write(self, path: str, data: bytes, *, mode: int = 0o644) -> None:
        resolved = resolve_posix_path(self.workdir, path)
        params = {**self._team_query}
        response = await self._client.put(
            f"/v1/sandboxes/{self._sandbox_id}/files",
            params=params,
            json={
                "path": resolved,
                "content": b64encode(data).decode("ascii"),
                "encoding": "base64",
                "mode": oct(mode),
            },
        )
        raise_for_status("vercel", response)

    async def ls(self, path: str) -> list[str]:
        resolved = resolve_posix_path(self.workdir, path)
        params = {
            "path": resolved,
            "list": "true",
        } | self._team_query
        response = await self._client.get(
            f"/v1/sandboxes/{self._sandbox_id}/files",
            params=params,
        )
        raise_for_status("vercel", response)
        payload = response.json()
        entries: list[str] = []
        if isinstance(payload, dict):
            raw = payload.get("entries") or payload.get("files") or []
        else:
            raw = payload if isinstance(payload, list) else []
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
                "Vercel synchronous exec endpoint does not accept stdin; "
                "use the WebSocket exec channel for interactive workloads.",
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
        params = {**self._team_query}

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    f"/v1/sandboxes/{self._sandbox_id}/exec",
                    params=params,
                    json=body,
                    timeout=float(effective_timeout) + 5.0,
                ),
                timeout=effective_timeout + 10,
            )
        except TimeoutError:
            raise TimeoutError(f"Command {cmd!r} timed out after {effective_timeout}s") from None
        raise_for_status("vercel", response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected vercel exec payload: {payload!r}")
        return ExecResult(
            exit_code=int(payload.get("exitCode", payload.get("exit_code", 0)) or 0),
            stdout=_decode_stream(payload.get("stdout"), payload.get("stdoutEncoding")),
            stderr=_decode_stream(payload.get("stderr"), payload.get("stderrEncoding")),
            duration_seconds=time.monotonic() - start,
        )

    async def snapshot(self) -> str:
        raise NotImplementedError(
            "Vercel Sandbox API does not expose a snapshot primitive at the "
            "time of writing; persist state via Vercel-managed storage instead.",
        )

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            response = await self._client.delete(
                f"/v1/sandboxes/{self._sandbox_id}",
                params={**self._team_query},
            )
            if response.status_code not in (200, 202, 204, 404):
                raise_for_status("vercel", response)
        except Exception as exc:
            logger.debug("Vercel sandbox %s teardown raised: %s", self._sandbox_id, exc)
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


def _team_query(team_id: str | None) -> dict[str, str]:
    return {"teamId": team_id} if team_id else {}


class VercelSandboxBackend:
    """Cloud :class:`SandboxBackend` powered by the Vercel Sandbox API."""

    name = "vercel"
    capabilities: frozenset[SandboxCapability] = frozenset(
        {
            SandboxCapability.FILE_RW,
            SandboxCapability.EXEC,
            SandboxCapability.NETWORK,
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
                :class:`httpx.AsyncClient` for tests; receives the
                resolved :class:`HttpClientSpec`.
            tls: Optional mTLS configuration for self-hosted Vercel
                environments behind a private CA. The public Vercel
                control plane uses public PKI.
        """
        self._client_factory = client_factory
        self._tls = tls
        self._sessions: dict[str, VercelSandboxSession] = {}

    async def create(
        self,
        manifest: WorkspaceManifest,
        options: dict[str, Any] | None = None,
    ) -> SandboxSession:
        """Provision a Vercel Sandbox.

        Recognised ``options``:

        - ``runtime``: Vercel sandbox runtime image (default ``node22``).
        - ``region``: Vercel region (e.g. ``iad1``).
        - ``team_id``: Override of ``VERCEL_TEAM_ID``.
        - ``session_id``: Explicit session identifier.
        """
        opts = dict(options or {})
        env = require_env("vercel", ("VERCEL_TOKEN",))
        api_url = opts.get("api_url") or os.environ.get("VERCEL_API_URL") or _DEFAULT_API_URL
        team_id = opts.get("team_id") or os.environ.get("VERCEL_TEAM_ID")
        session_id = allocate_session_id("bernstein-vercel", opts.get("session_id"))
        spec = HttpClientSpec(
            base_url=str(api_url).rstrip("/"),
            headers={
                "Authorization": f"Bearer {env['VERCEL_TOKEN']}",
                "Accept": "application/json",
                "User-Agent": "bernstein-sandbox/1.0",
            },
            timeout=float(manifest.timeout_seconds + 30),
            tls=self._tls,
        )
        client = self._client_factory(spec=spec) if self._client_factory is not None else build_async_client(spec)
        body: dict[str, Any] = {
            "name": session_id,
            "runtime": opts.get("runtime", _DEFAULT_RUNTIME),
            "region": opts.get("region"),
            "workdir": manifest.root,
            "env": dict(manifest.env),
            "timeout": manifest.timeout_seconds,
        }
        params = _team_query(team_id)
        try:
            response = await client.post("/v1/sandboxes", params=params, json=body)
        except Exception:
            await client.aclose()
            raise
        raise_for_status("vercel", response)
        payload = response.json()
        sandbox_id = (payload.get("id") if isinstance(payload, dict) else None) or session_id
        session = VercelSandboxSession(
            session_id=session_id,
            sandbox_id=str(sandbox_id),
            client=client,
            team_query=params,
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
            "Vercel backend does not declare SNAPSHOT capability; Vercel Sandbox does not expose snapshot/resume.",
        )

    async def destroy(self, session: SandboxSession) -> None:
        await session.shutdown()
        self._sessions.pop(session.session_id, None)


__all__ = [
    "VercelSandboxBackend",
    "VercelSandboxSession",
]
