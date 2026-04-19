"""Integration tests for :class:`R2ArtifactSink` (oai-003).

R2 has no widely-available local emulator, so these tests require
real Cloudflare credentials:

- ``R2_ACCOUNT_ID``
- ``R2_ACCESS_KEY_ID``
- ``R2_SECRET_ACCESS_KEY``
- ``BERNSTEIN_R2_TEST_BUCKET``

When any of the above is missing the module skips.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from bernstein.core.storage import ArtifactSinkConformance

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bernstein.core.storage.sink import ArtifactSink


def _r2_available() -> bool:
    if importlib.util.find_spec("boto3") is None:
        return False
    if os.environ.get("BERNSTEIN_SKIP_R2_TESTS") == "1":
        return False
    return bool(
        os.environ.get("R2_ACCOUNT_ID")
        and os.environ.get("R2_ACCESS_KEY_ID")
        and os.environ.get("R2_SECRET_ACCESS_KEY")
        and os.environ.get("BERNSTEIN_R2_TEST_BUCKET"),
    )


pytestmark = pytest.mark.skipif(
    not _r2_available(),
    reason="R2 credentials missing",
)


class TestR2SinkConformance(ArtifactSinkConformance):
    """Runs the shared conformance suite against a live R2 bucket."""

    large_payload_bytes = 256 * 1024

    @pytest_asyncio.fixture
    async def sink(self) -> AsyncIterator[ArtifactSink]:
        from bernstein.core.storage.sinks.r2 import R2ArtifactSink

        bucket = os.environ["BERNSTEIN_R2_TEST_BUCKET"]
        prefix = f"it/{uuid.uuid4()}"
        s = R2ArtifactSink(bucket=bucket, prefix=prefix)
        try:
            yield s
        finally:
            try:
                for k in await s.list(""):
                    await s.delete(k)
            finally:
                await s.close()
