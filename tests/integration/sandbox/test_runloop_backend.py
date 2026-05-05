"""Integration tests for the Runloop sandbox backend.

Gated on ``CI_RUNLOOP_TEST=1`` plus ``RUNLOOP_API_KEY``. Without the
gate the suite skips cleanly.
"""

from __future__ import annotations

import os

import pytest

from bernstein.core.sandbox import (
    SandboxCapability,
    WorkspaceManifest,
)


def _gate_ready() -> bool:
    if os.environ.get("CI_RUNLOOP_TEST") != "1":
        return False
    return bool(os.environ.get("RUNLOOP_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _gate_ready(),
    reason="CI_RUNLOOP_TEST or RUNLOOP_API_KEY not set",
)


@pytest.mark.asyncio
async def test_runloop_smoke_session_lifecycle() -> None:
    from bernstein.core.sandbox.backends.runloop import RunloopSandboxBackend

    backend = RunloopSandboxBackend()
    assert SandboxCapability.SNAPSHOT in backend.capabilities

    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=120)
    session = await backend.create(manifest)
    try:
        result = await session.exec(["sh", "-c", "echo runloop-ok"])
        assert result.exit_code == 0
        assert b"runloop-ok" in result.stdout
    finally:
        await backend.destroy(session)
