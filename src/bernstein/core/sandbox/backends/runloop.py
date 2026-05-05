"""Runloop cloud sandbox backend (optional extra).

Runloop (https://runloop.ai) provides AI-tailored sandbox runtimes
("devboxes") with snapshotting, file-system primitives, and an exec
endpoint. This backend speaks the public REST API directly via
:mod:`httpx`.

Environment variables
---------------------

- ``RUNLOOP_API_KEY`` — required. Bearer token from the Runloop
  Dashboard.
- ``RUNLOOP_API_URL`` — optional override of the API root. Defaults to
  ``https://api.runloop.ai/v1``.
- ``RUNLOOP_PROJECT_ID`` — optional default project id forwarded as
  ``project_id`` on devbox creation.

Capabilities
------------

`FILE_RW`, `EXEC`, `NETWORK`, `SNAPSHOT`. Runloop devboxes can be
snapshotted (``POST /devboxes/{id}/snapshot``) and resumed (``POST
/devboxes`` with ``snapshot_id``).

Honest limitations
------------------

- The synchronous exec endpoint blocks until the command completes;
  for long-running streamed output use Runloop's WebSocket exec
  channel which is not yet plumbed through this backend.
- Stdin injection is not part of the synchronous exec request
  envelope; passing ``stdin=`` raises :class:`NotImplementedError`.
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

_DEFAULT_API_URL = "https://api.runloop.ai/v1"


class RunloopSandboxSession(SandboxSession):
    """Session backed by a Runloop devbox provisioned via REST."""

    backend_name = "runloop"

    def __init__(
        self,
        *,
        session_id: str,
        devbox_id: str,
        client: httpx.AsyncClient,
        workdir: str,
        base_env: Mapping[str, str],
        default_timeout: int,
    ) -> None:
        self.session_id = session_id
        self.workdir = workdir
        self._devbox_id = devbox_id
        self._client = client
        self._base_env = dict(base_env)
        self._default_timeout = default_timeout
        self._closed = False

    async def read(self, path: str) -> bytes:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.get(
            f"/devboxes/{self._devbox_id}/read_file_contents",
            params={"file_path": resolved},
        )
        raise_for_status("runloop", response)
        payload = response.json()
        if isinstance(payload, dict):
            content = payload.get("contents") or payload.get("content")
            encoding = payload.get("encoding")
            if isinstance(content, str):
                if encoding == "base64":
                    return b64decode(content)
                return content.encode("utf-8")
        return response.content

    async def write(self, path: str, data: bytes, *, mode: int = 0o644) -> None:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.post(
            f"/devboxes/{self._devbox_id}/write_file",
            json={
                "file_path": resolved,
                "contents": b64encode(data).decode("ascii"),
                "encoding": "base64",
                "mode": oct(mode),
            },
        )
        raise_for_status("runloop", response)

    async def ls(self, path: str) -> list[str]:
        resolved = resolve_posix_path(self.workdir, path)
        response = await self._client.post(
            f"/devboxes/{self._devbox_id}/execute_sync",
            json={"command": f"ls -1 {_quote(resolved)}", "shell": True},
        )
        raise_for_status("runloop", response)
        payload = response.json()
        stdout = payload.get("stdout", "") if isinstance(payload, dict) else ""
        if isinstance(stdout, str):
            return sorted([line for line in stdout.splitlines() if line])
        return []

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
                "Runloop synchronous exec endpoint does not accept stdin; "
                "use the WebSocket exec channel for interactive workloads.",
            )
        effective_cwd = cwd if cwd is not None else self.workdir
        effective_timeout = timeout if timeout is not None else self._default_timeout
        merged_env = merge_exec_env(self._base_env, env)
        body: dict[str, Any] = {
            "command": " ".join(_quote(part) for part in cmd),
            "shell": True,
            "shell_name": "bash",
            "working_directory": effective_cwd,
            "env_vars": merged_env,
            "timeout_seconds": effective_timeout,
        }

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    f"/devboxes/{self._devbox_id}/execute_sync",
                    json=body,
                    timeout=float(effective_timeout) + 5.0,
                ),
                timeout=effective_timeout + 10,
            )
        except TimeoutError:
            raise TimeoutError(f"Command {cmd!r} timed out after {effective_timeout}s") from None
        raise_for_status("runloop", response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected runloop exec payload: {payload!r}")
        return ExecResult(
            exit_code=int(payload.get("exit_status", payload.get("exit_code", 0)) or 0),
            stdout=_decode_stream(payload.get("stdout"), payload.get("stdout_encoding")),
            stderr=_decode_stream(payload.get("stderr"), payload.get("stderr_encoding")),
            duration_seconds=time.monotonic() - start,
        )

    async def snapshot(self) -> str:
        response = await self._client.post(
            f"/devboxes/{self._devbox_id}/snapshot_disk",
            json={"name": f"bernstein-{self.session_id}"},
        )
        raise_for_status("runloop", response)
        payload = response.json()
        if isinstance(payload, dict):
            for key in ("id", "snapshot_id", "snapshotId"):
                value = payload.get(key)
                if isinstance(value, str):
                    return value
        raise RuntimeError(f"Runloop snapshot did not return an id: {payload!r}")

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            response = await self._client.post(
                f"/devboxes/{self._devbox_id}/shutdown",
            )
            if response.status_code not in (200, 202, 204, 404):
                raise_for_status("runloop", response)
        except Exception as exc:
            logger.debug("Runloop devbox %s teardown raised: %s", self._devbox_id, exc)
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


def _quote(arg: str) -> str:
    """POSIX-shell quote a single argv element."""
    if not arg:
        return "''"
    safe = all(ch.isalnum() or ch in "@%+=:,./-" for ch in arg)
    if safe:
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


class RunloopSandboxBackend:
    """Cloud :class:`SandboxBackend` powered by the Runloop REST API."""

    name = "runloop"
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
                :class:`httpx.AsyncClient` for tests; receives the
                resolved :class:`HttpClientSpec`.
            tls: Optional mTLS configuration for self-hosted control
                planes behind a private CA.
        """
        self._client_factory = client_factory
        self._tls = tls
        self._sessions: dict[str, RunloopSandboxSession] = {}

    async def create(
        self,
        manifest: WorkspaceManifest,
        options: dict[str, Any] | None = None,
    ) -> SandboxSession:
        """Provision a new Runloop devbox.

        Recognised ``options``:

        - ``blueprint``: Runloop blueprint id (image preset).
        - ``project_id``: Override of ``RUNLOOP_PROJECT_ID``.
        - ``session_id``: Explicit session identifier.
        - ``launch_parameters``: Free-form dict forwarded to the API.
        """
        opts = dict(options or {})
        env = require_env("runloop", ("RUNLOOP_API_KEY",))
        api_url = opts.get("api_url") or os.environ.get("RUNLOOP_API_URL") or _DEFAULT_API_URL
        project_id = opts.get("project_id") or os.environ.get("RUNLOOP_PROJECT_ID")
        session_id = allocate_session_id("bernstein-runloop", opts.get("session_id"))
        spec = HttpClientSpec(
            base_url=str(api_url).rstrip("/"),
            headers={
                "Authorization": f"Bearer {env['RUNLOOP_API_KEY']}",
                "Accept": "application/json",
                "User-Agent": "bernstein-sandbox/1.0",
            },
            timeout=float(manifest.timeout_seconds + 30),
            tls=self._tls,
        )
        client = self._client_factory(spec=spec) if self._client_factory is not None else build_async_client(spec)
        body: dict[str, Any] = {
            "name": session_id,
            "environment_variables": dict(manifest.env),
        }
        if project_id:
            body["project_id"] = project_id
        if "blueprint" in opts:
            body["blueprint_id"] = opts["blueprint"]
        if "launch_parameters" in opts:
            body["launch_parameters"] = opts["launch_parameters"]
        try:
            response = await client.post("/devboxes", json=body)
        except Exception:
            await client.aclose()
            raise
        raise_for_status("runloop", response)
        payload = response.json()
        devbox_id = (payload.get("id") if isinstance(payload, dict) else None) or session_id
        session = RunloopSandboxSession(
            session_id=session_id,
            devbox_id=str(devbox_id),
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
        env = require_env("runloop", ("RUNLOOP_API_KEY",))
        api_url = os.environ.get("RUNLOOP_API_URL") or _DEFAULT_API_URL
        spec = HttpClientSpec(
            base_url=str(api_url).rstrip("/"),
            headers={
                "Authorization": f"Bearer {env['RUNLOOP_API_KEY']}",
                "Accept": "application/json",
                "User-Agent": "bernstein-sandbox/1.0",
            },
            timeout=120.0,
            tls=self._tls,
        )
        client = self._client_factory(spec=spec) if self._client_factory is not None else build_async_client(spec)
        try:
            response = await client.post(
                "/devboxes",
                json={"name": f"bernstein-resume-{snapshot_id}", "snapshot_id": snapshot_id},
            )
        except Exception:
            await client.aclose()
            raise
        raise_for_status("runloop", response)
        payload = response.json()
        devbox_id = (payload.get("id") if isinstance(payload, dict) else None) or snapshot_id
        session = RunloopSandboxSession(
            session_id=f"resume-{snapshot_id}",
            devbox_id=str(devbox_id),
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
    "RunloopSandboxBackend",
    "RunloopSandboxSession",
]
