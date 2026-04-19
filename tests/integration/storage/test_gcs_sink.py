"""Integration tests for :class:`GCSArtifactSink` (oai-003).

Gated by:

- ``google-cloud-storage`` must be importable.
- ``STORAGE_EMULATOR_HOST`` points at fake-gcs-server, or
  ``GOOGLE_APPLICATION_CREDENTIALS`` points at a real service-account
  JSON and ``BERNSTEIN_GCS_TEST_BUCKET`` names a real bucket.

Run locally with::

    docker run -d -p 4443:4443 fsouza/fake-gcs-server
    STORAGE_EMULATOR_HOST=http://localhost:4443 \\
    BERNSTEIN_GCS_TEST_BUCKET=bernstein-it \\
    uv run pytest tests/integration/storage/test_gcs_sink.py -x -q
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


def _gcs_available() -> bool:
    try:
        if importlib.util.find_spec("google.cloud.storage") is None:
            return False
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
    if os.environ.get("BERNSTEIN_SKIP_GCS_TESTS") == "1":
        return False
    if os.environ.get("STORAGE_EMULATOR_HOST"):
        return True
    return bool(
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.environ.get("BERNSTEIN_GCS_TEST_BUCKET"),
    )


pytestmark = pytest.mark.skipif(
    not _gcs_available(),
    reason="google-cloud-storage not installed or GCS endpoint/credentials missing",
)


class TestGCSSinkConformance(ArtifactSinkConformance):
    """Runs the shared conformance suite against a live (emulated) GCS."""

    large_payload_bytes = 256 * 1024

    @pytest_asyncio.fixture
    async def sink(self) -> AsyncIterator[ArtifactSink]:
        from bernstein.core.storage.sinks.gcs import GCSArtifactSink

        bucket = os.environ.get("BERNSTEIN_GCS_TEST_BUCKET", "bernstein-test")
        prefix = f"it/{uuid.uuid4()}"
        s = GCSArtifactSink(bucket=bucket, prefix=prefix)
        try:
            yield s
        finally:
            try:
                for k in await s.list(""):
                    await s.delete(k)
            finally:
                await s.close()
