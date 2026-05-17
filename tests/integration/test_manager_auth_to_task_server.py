"""End-to-end regression for #1261 — manager auth to the task server.

Boots a real uvicorn server with the legacy bearer token enabled, then
spawns a subprocess in a *worktree-like* cwd (a subdir of the
orchestrator workdir, mirroring the runtime layout the production
spawner produces). The subprocess receives exactly the env that
:func:`bernstein.adapters.env_isolation.build_filtered_env` would hand a
real agent — i.e. the ``BERNSTEIN_AUTH_TOKEN`` env var passes through
the allowlist — and is given the prompt fragment :func:`_render_auth_section`
would inject.

The subprocess then POSTs to ``/tasks`` using both auth channels:

1. The Bearer header sourced from the absolute path embedded in the
   prompt (the primary channel — broken before this fix, because the
   path could resolve as relative and miss the real token file when the
   spawn cwd was a git worktree).
2. The Bearer header sourced from ``$BERNSTEIN_AUTH_TOKEN`` (the
   documented fallback — also surfaced in the auth section after the
   fix).

Without the #1261 fix the first POST returns 401 (path resolves against
the worktree, ``cat`` reads no file, openssl signs empty body, Bearer is
literally the empty string). With the fix both POSTs return 201.

Skipped on Windows for the same reason as the federation suite — uvicorn
+ asyncio fixture mechanics are fragile under ProactorEventLoop.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from bernstein.adapters.env_isolation import build_filtered_env
from bernstein.core.agents.spawner_core import (
    AgentSpawner,
    _render_auth_section,
)
from bernstein.core.server import create_app

if TYPE_CHECKING:
    from bernstein.core.models import ModelConfig

    from bernstein.adapters.base import SpawnResult

pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="uvicorn + asyncio + httpx fixtures fragile on Windows CI runners",
    ),
    # The whole module exercises the real auth middleware — opt out of the
    # global ``BERNSTEIN_AUTH_DISABLED=1`` test default (see tests/conftest.py).
    pytest.mark.auth_enabled,
]


_BEARER_TOKEN = "regression-1261-bearer"


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _AuthServer:
    """A FastAPI task server running uvicorn in a background thread."""

    def __init__(self, app: FastAPI, port: int) -> None:
        self.app = app
        self.port = port
        self.endpoint = f"http://127.0.0.1:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self, *, timeout: float = 10.0) -> None:
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            lifespan="off",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name=f"taskserver-{self.port}")
        thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not server.started:
            time.sleep(0.02)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=2)
            raise RuntimeError(f"task server on port {self.port} did not start within {timeout}s")
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture
def auth_server(tmp_path: Path) -> Generator[_AuthServer, None, None]:
    """Start a task server with the legacy bearer token wired through."""
    jsonl_path = tmp_path / "server" / "tasks.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    app = create_app(jsonl_path=jsonl_path, auth_token=_BEARER_TOKEN)
    server = _AuthServer(app, _free_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_server_rejects_unauthenticated_post(auth_server: _AuthServer) -> None:
    """Sanity guard: the task server actually requires the bearer.

    Without this assertion the rest of the suite could silently pass even
    if the middleware were broken (e.g. accidentally letting anonymous
    POSTs through).
    """
    resp = httpx.post(
        f"{auth_server.endpoint}/tasks",
        json={
            "title": "should-be-rejected",
            "role": "backend",
            "description": "anonymous post — must 401 before validation runs",
        },
        timeout=5.0,
    )
    assert resp.status_code == 401, resp.text


def test_manager_subprocess_can_post_via_token_file(
    tmp_path: Path,
    auth_server: _AuthServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subprocess in worktree cwd reads the absolute token path → POST succeeds.

    Reproduces the #1261 setup: orchestrator workdir = ``tmp/project``,
    spawner issues a token under ``project/.sdd/runtime/agent_tokens/``,
    auth section embeds the absolute path, subprocess runs with
    cwd=worktree (a sibling subdir) and uses ``cat $TOKEN_PATH`` to
    extract the bearer. Asserts the server returns 201.
    """
    # 1. Project root + worktree layout
    project_root = tmp_path / "project"
    project_root.mkdir()
    worktree_dir = project_root / ".sdd" / "worktrees" / "manager-deadbeef"
    worktree_dir.mkdir(parents=True)

    # 2. Build the spawner with a *relative* workdir to exercise the
    #    absolutisation guarantee of the fix.
    monkeypatch.chdir(tmp_path)
    spawner = _make_offline_spawner(Path("project"))
    spawner._identity_store_instance = MagicMock(  # pyright: ignore[reportPrivateUsage]
        create_identity=MagicMock(return_value=(MagicMock(), _BEARER_TOKEN)),
    )

    # 3. Issue the token + render the auth section the same way the
    #    production spawner does.
    token_path = spawner._issue_agent_token(  # pyright: ignore[reportPrivateUsage]
        session_id="manager-deadbeef",
        role="manager",
        task_ids=["T-001"],
    )
    auth_section = _render_auth_section(token_path)

    # The fix invariant: the path embedded in the prompt is absolute.
    assert token_path.is_absolute(), token_path
    assert str(token_path) in auth_section

    # 4. Build the env the env_isolation layer would hand the agent.
    #    BERNSTEIN_AUTH_TOKEN passes through the allowlist.
    monkeypatch.setenv("BERNSTEIN_AUTH_TOKEN", _BEARER_TOKEN)
    agent_env = build_filtered_env()
    assert agent_env.get("BERNSTEIN_AUTH_TOKEN") == _BEARER_TOKEN

    # 5. The subprocess script: reads the token from the absolute path
    #    embedded in the prompt and POSTs to /tasks. Mirrors what the
    #    manager would do via curl, only in Python so we do not depend on
    #    openssl / curl being on the test host's PATH.
    script = textwrap.dedent(
        f"""
        import json
        import os
        import sys
        import urllib.request

        # Sanity: the subprocess cwd is the worktree, NOT the project root.
        assert os.getcwd().endswith("manager-deadbeef"), os.getcwd()

        token_path = {str(token_path)!r}
        with open(token_path) as fh:
            token = fh.read().strip()

        req = urllib.request.Request(
            "{auth_server.endpoint}/tasks",
            method="POST",
            data=json.dumps({{"title": "subtask-via-file", "role": "backend",
                              "description": "issue-1261 regression"}}).encode(),
            headers={{
                "Authorization": f"Bearer {{token}}",
                "Content-Type": "application/json",
            }},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(resp.status)
        except urllib.error.HTTPError as exc:
            print(exc.code)
            sys.exit(1)
        """
    ).strip()

    # 6. Run the subprocess in the worktree cwd with the agent env.
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=worktree_dir,
        env=agent_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        f"Manager subprocess failed to POST /tasks.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert result.stdout.strip() == "201", result.stdout


def test_manager_subprocess_can_post_via_env_var_fallback(
    tmp_path: Path,
    auth_server: _AuthServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``BERNSTEIN_AUTH_TOKEN`` env fallback also reaches the server with 201.

    Defence-in-depth half of #1261 — if the per-agent token file is
    missing or unreadable, the env var (passed through the
    env_isolation allowlist) must still authenticate the request. This
    guards against silent regressions in the env allowlist as much as in
    the path-resolution fix.
    """
    project_root = tmp_path / "project-env"
    project_root.mkdir()
    worktree_dir = project_root / ".sdd" / "worktrees" / "manager-cafefeed"
    worktree_dir.mkdir(parents=True)

    monkeypatch.setenv("BERNSTEIN_AUTH_TOKEN", _BEARER_TOKEN)
    agent_env = build_filtered_env()
    assert agent_env.get("BERNSTEIN_AUTH_TOKEN") == _BEARER_TOKEN

    script = textwrap.dedent(
        f"""
        import json
        import os
        import sys
        import urllib.request

        token = os.environ["BERNSTEIN_AUTH_TOKEN"]
        req = urllib.request.Request(
            "{auth_server.endpoint}/tasks",
            method="POST",
            data=json.dumps({{"title": "subtask-via-env", "role": "backend",
                              "description": "issue-1261 env fallback"}}).encode(),
            headers={{
                "Authorization": f"Bearer {{token}}",
                "Content-Type": "application/json",
            }},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(resp.status)
        except urllib.error.HTTPError as exc:
            print(exc.code)
            sys.exit(1)
        """
    ).strip()

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=worktree_dir,
        env=agent_env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, f"Env-fallback subprocess failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert result.stdout.strip() == "201", result.stdout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _NoopAdapter:
    """Adapter shell — :func:`_make_offline_spawner` never invokes spawn()."""

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict | None = None,  # type: ignore[type-arg]
        timeout_seconds: int = 1800,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> SpawnResult:
        raise NotImplementedError

    def name(self) -> str:
        return "noop"

    def is_alive(self, pid: int) -> bool:  # pragma: no cover — never invoked
        return False

    def kill(self, pid: int) -> None:  # pragma: no cover — never invoked
        pass


def _make_offline_spawner(workdir: Path) -> AgentSpawner:
    """Construct a spawner with worktree creation disabled."""
    templates_dir = workdir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    return AgentSpawner(
        adapter=_NoopAdapter(),  # type: ignore[arg-type]
        templates_dir=templates_dir,
        workdir=workdir,
        use_worktrees=False,
    )
