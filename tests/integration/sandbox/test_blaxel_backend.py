"""Integration tests for the Blaxel sandbox backend.

Gated on ``CI_BLAXEL_TEST=1`` plus the live credentials in the docs:

- ``BLAXEL_API_KEY``
- ``BLAXEL_WORKSPACE``
- (optional) ``BLAXEL_API_URL``

Without the gate the suite skips cleanly so day-to-day local pytest
runs do not depend on a paid provider account. Mirrors the
``CI_TUNNEL_TEST`` pattern used by the cluster mTLS workflow.
"""

from __future__ import annotations

import os

import pytest

from bernstein.core.sandbox import (
    SandboxCapability,
    WorkspaceManifest,
)


def _gate_ready() -> bool:
    if os.environ.get("CI_BLAXEL_TEST") != "1":
        return False
    return bool(os.environ.get("BLAXEL_API_KEY") and os.environ.get("BLAXEL_WORKSPACE"))


pytestmark = pytest.mark.skipif(
    not _gate_ready(),
    reason="CI_BLAXEL_TEST or BLAXEL_API_KEY/BLAXEL_WORKSPACE not set",
)


@pytest.mark.asyncio
async def test_blaxel_smoke_session_lifecycle() -> None:
    """Provision, exec a trivial command, tear down."""
    from bernstein.core.sandbox.backends.blaxel import BlaxelSandboxBackend

    backend = BlaxelSandboxBackend()
    assert SandboxCapability.EXEC in backend.capabilities

    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=120)
    session = await backend.create(manifest)
    try:
        result = await session.exec(["sh", "-c", "echo blaxel-ok"])
        assert result.exit_code == 0
        assert b"blaxel-ok" in result.stdout
    finally:
        await backend.destroy(session)
