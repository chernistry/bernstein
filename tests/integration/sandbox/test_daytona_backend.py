"""Integration tests for the Daytona sandbox backend.

Gated on ``CI_DAYTONA_TEST=1`` plus ``DAYTONA_API_KEY``. Without the
gate the suite skips so day-to-day local pytest runs do not depend on
a paid provider account.
"""

from __future__ import annotations

import os

import pytest

from bernstein.core.sandbox import (
    SandboxCapability,
    WorkspaceManifest,
)


def _gate_ready() -> bool:
    if os.environ.get("CI_DAYTONA_TEST") != "1":
        return False
    return bool(os.environ.get("DAYTONA_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _gate_ready(),
    reason="CI_DAYTONA_TEST or DAYTONA_API_KEY not set",
)


@pytest.mark.asyncio
async def test_daytona_smoke_session_lifecycle() -> None:
    from bernstein.core.sandbox.backends.daytona import DaytonaSandboxBackend

    backend = DaytonaSandboxBackend()
    assert SandboxCapability.SNAPSHOT in backend.capabilities

    manifest = WorkspaceManifest(root="/workspace", timeout_seconds=120)
    session = await backend.create(manifest)
    try:
        result = await session.exec(["sh", "-c", "echo daytona-ok"])
        assert result.exit_code == 0
        assert b"daytona-ok" in result.stdout
    finally:
        await backend.destroy(session)
