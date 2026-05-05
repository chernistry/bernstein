"""Integration tests for the Vercel sandbox backend.

Gated on ``CI_VERCEL_TEST=1`` plus ``VERCEL_TOKEN`` (and optionally
``VERCEL_TEAM_ID``). Without the gate the suite skips cleanly.
"""

from __future__ import annotations

import os

import pytest

from bernstein.core.sandbox import (
    SandboxCapability,
    WorkspaceManifest,
)


def _gate_ready() -> bool:
    if os.environ.get("CI_VERCEL_TEST") != "1":
        return False
    return bool(os.environ.get("VERCEL_TOKEN"))


pytestmark = pytest.mark.skipif(
    not _gate_ready(),
    reason="CI_VERCEL_TEST or VERCEL_TOKEN not set",
)


@pytest.mark.asyncio
async def test_vercel_smoke_session_lifecycle() -> None:
    from bernstein.core.sandbox.backends.vercel import VercelSandboxBackend

    backend = VercelSandboxBackend()
    assert SandboxCapability.EXEC in backend.capabilities

    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=120)
    session = await backend.create(manifest)
    try:
        result = await session.exec(["sh", "-c", "echo vercel-ok"])
        assert result.exit_code == 0
        assert b"vercel-ok" in result.stdout
    finally:
        await backend.destroy(session)
