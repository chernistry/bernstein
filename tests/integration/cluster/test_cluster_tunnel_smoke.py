"""Smoke test: Bernstein cluster reachable through a tunnel sidecar.

This test brings up a 2-container compose:

  * ``bernstein-central``     - the cluster central server
  * ``bernstein-cloudflared`` - a ``cloudflared`` sidecar terminating
    the public tunnel hostname

…and asserts that a Bernstein worker, configured with the public
tunnel URL, can register and heartbeat through the tunnel.

It is **opt-in**:

  * Skipped by default in PR runs.
  * The CI nightly schedule runs it via the ``cluster-tunnel-e2e.yml``
    workflow, which sets ``CI_TUNNEL_TEST=1``.
  * Requires a Cloudflare tunnel token in ``CF_TUNNEL_TOKEN`` and the
    public hostname in ``CF_TUNNEL_HOSTNAME``.

The test is intentionally minimal: tunnel up → register → heartbeat →
deregister. Anything more invasive belongs in ``test_real_2node.py``,
which already exercises crash recovery, partitions, and token expiry
on the local loopback path.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import httpx
import pytest

from bernstein.core.security.jwt_tokens import JWTManager, JWTPayload

# --------------------------------------------------------------------------- #
# Opt-in gate
# --------------------------------------------------------------------------- #

_TUNNEL_TEST_ENABLED = os.environ.get("CI_TUNNEL_TEST", "").lower() in ("1", "true", "yes")
_CF_TOKEN = os.environ.get("CF_TUNNEL_TOKEN", "")
_CF_HOSTNAME = os.environ.get("CF_TUNNEL_HOSTNAME", "")

pytestmark = [
    pytest.mark.cluster_e2e,
    pytest.mark.slow,
    pytest.mark.skipif(
        not _TUNNEL_TEST_ENABLED,
        reason="cluster tunnel smoke test is opt-in: set CI_TUNNEL_TEST=1",
    ),
    pytest.mark.skipif(
        not _CF_TOKEN or not _CF_HOSTNAME,
        reason="CF_TUNNEL_TOKEN and CF_TUNNEL_HOSTNAME must be set (GitHub secrets in CI)",
    ),
    pytest.mark.skipif(
        shutil.which("docker") is None,
        reason="docker CLI is required to bring the compose stack up",
    ),
]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "examples" / "cluster" / "cloudflared" / "docker-compose.yml"
TUNNEL_READY_TIMEOUT_S = 120.0
TUNNEL_READY_POLL_S = 2.0
HEARTBEAT_OBSERVATION_WINDOW_S = 15.0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _compose(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose`` against the example compose file."""
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | (env or {}),
    )


def _wait_tunnel_ready(public_url: str, timeout_s: float = TUNNEL_READY_TIMEOUT_S) -> bool:
    """Poll the public tunnel hostname until ``/health`` returns 200."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with suppress(httpx.HTTPError):
            resp = httpx.get(f"{public_url}/health", timeout=5.0)
            if resp.status_code == 200:
                return True
        time.sleep(TUNNEL_READY_POLL_S)
    return False


def _mint_token(secret: str, node_id: str, scopes: list[str]) -> str:
    """Mint a cluster JWT signed with ``secret`` for ``node_id``."""
    mgr = JWTManager(secret=secret, expiry_hours=1)
    now = time.time()
    payload = JWTPayload(
        session_id=f"node-{node_id}",
        user_id=node_id,
        issued_at=now,
        expires_at=now + 3600.0,
        scopes=scopes,
    )
    return mgr._encode(payload)


# --------------------------------------------------------------------------- #
# Fixture: bring the compose stack up + down
# --------------------------------------------------------------------------- #


@pytest.fixture
def tunnel_stack(tmp_path: Path) -> Iterator[dict[str, str]]:
    """Bring up cloudflared + central, yield connection info, tear down."""
    cluster_secret = secrets.token_urlsafe(32)
    public_url = f"https://{_CF_HOSTNAME}"

    env = {
        "CF_TUNNEL_TOKEN": _CF_TOKEN,
        "BERNSTEIN_CLUSTER_AUTH_SECRET": cluster_secret,
    }

    up = _compose("up", "-d", "--build", env=env)
    if up.returncode != 0:
        pytest.fail(f"docker compose up failed:\nstdout:\n{up.stdout}\nstderr:\n{up.stderr}")

    try:
        if not _wait_tunnel_ready(public_url):
            logs = _compose("logs", "--no-color", env=env).stdout
            log_path = tmp_path / "compose.log"
            log_path.write_text(logs, encoding="utf-8")
            pytest.fail(
                f"Tunnel did not become reachable at {public_url} within "
                f"{TUNNEL_READY_TIMEOUT_S:.0f}s. Logs at {log_path}."
            )
        yield {
            "public_url": public_url,
            "cluster_secret": cluster_secret,
        }
    finally:
        # Always capture logs on the way down so CI can publish them
        # as an artifact; keep the operation best-effort.
        logs_proc = _compose("logs", "--no-color", env=env)
        log_path = tmp_path / "compose.log"
        log_path.write_text(logs_proc.stdout or "", encoding="utf-8")
        _compose("down", "-v", "--remove-orphans", env=env)


# --------------------------------------------------------------------------- #
# Test
# --------------------------------------------------------------------------- #


def test_worker_registers_and_heartbeats_via_tunnel(tunnel_stack: dict[str, str]) -> None:
    """A worker reaching the central via Cloudflare Tunnel registers + heartbeats."""
    public_url = tunnel_stack["public_url"]
    secret = tunnel_stack["cluster_secret"]

    register_token = _mint_token(secret, "smoke-worker", ["node:register"])
    register_payload = {
        "name": "smoke-worker",
        "url": "",
        "capacity": {
            "max_agents": 1,
            "available_slots": 1,
            "active_agents": 0,
            "gpu_available": False,
            "supported_models": ["sonnet"],
        },
        "labels": {"deployment": "tunnel-smoke"},
        "cell_ids": [],
    }

    with httpx.Client(timeout=10.0) as cli:
        # ---- registration ------------------------------------------------ #
        register = cli.post(
            f"{public_url}/cluster/nodes",
            json=register_payload,
            headers={"Authorization": f"Bearer {register_token}"},
        )
        assert register.status_code == 201, f"register failed: {register.status_code} {register.text}"
        node_id = register.json()["id"]
        assert isinstance(node_id, str) and node_id, "central did not return a node id"

        # ---- heartbeat loop --------------------------------------------- #
        # Five heartbeats over ~15s. We don't try to assert reaper
        # behaviour here - that's covered by the loopback suite. We
        # just want to confirm the tunnel carries POSTs, not just GETs.
        hb_token = _mint_token(secret, node_id, ["node:heartbeat"])
        deadline = time.monotonic() + HEARTBEAT_OBSERVATION_WINDOW_S
        ok_count = 0
        while time.monotonic() < deadline and ok_count < 5:
            hb = cli.post(
                f"{public_url}/cluster/nodes/{node_id}/heartbeat",
                json={"capacity": register_payload["capacity"]},
                headers={"Authorization": f"Bearer {hb_token}"},
            )
            assert hb.status_code in (200, 204), f"heartbeat failed: {hb.status_code} {hb.text}"
            ok_count += 1
            time.sleep(2.0)

        assert ok_count >= 3, f"only {ok_count} heartbeats succeeded through the tunnel"

        # ---- deregister -------------------------------------------------- #
        admin_token = _mint_token(secret, "smoke-admin", ["node:admin"])
        deregister = cli.delete(
            f"{public_url}/cluster/nodes/{node_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # Some builds return 200, others 204 - accept either.
        assert deregister.status_code in (200, 204), f"deregister failed: {deregister.status_code} {deregister.text}"
