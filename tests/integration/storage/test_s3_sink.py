"""Integration tests for :class:`S3ArtifactSink` (oai-003).

Gated by:

- ``boto3`` must be installable (so unit envs without the 's3' extra
  skip gracefully rather than ImportError).
- Either ``AWS_ENDPOINT_URL`` points at LocalStack, or real
  ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` are set.
- ``BERNSTEIN_S3_TEST_BUCKET`` names a bucket to scope test data to.

When the gate fails the whole module skips. Run locally with::

    docker run -d -p 4566:4566 localstack/localstack
    AWS_ENDPOINT_URL=http://localhost:4566 \\
    AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \\
    AWS_REGION=us-east-1 \\
    BERNSTEIN_S3_TEST_BUCKET=bernstein-it \\
    uv run pytest tests/integration/storage/test_s3_sink.py -x -q
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


def _s3_available() -> bool:
    if importlib.util.find_spec("boto3") is None:
        return False
    if os.environ.get("BERNSTEIN_SKIP_S3_TESTS") == "1":
        return False
    # Either LocalStack URL or explicit AWS creds must exist.
    if os.environ.get("AWS_ENDPOINT_URL"):
        return True
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )


pytestmark = pytest.mark.skipif(
    not _s3_available(),
    reason="boto3 not installed or S3 endpoint/credentials missing",
)


class TestS3SinkConformance(ArtifactSinkConformance):
    """Runs the shared conformance suite against a live (emulated) S3."""

    # Keep the large-payload test modest — 256 KB round-trips against
    # LocalStack in <100 ms.
    large_payload_bytes = 256 * 1024

    @pytest_asyncio.fixture
    async def sink(self) -> AsyncIterator[ArtifactSink]:
        """Yield an S3ArtifactSink scoped to a throw-away prefix."""
        from bernstein.core.storage.sinks.s3 import S3ArtifactSink

        bucket = os.environ.get("BERNSTEIN_S3_TEST_BUCKET", "bernstein-test")
        prefix = f"it/{uuid.uuid4()}"
        s = S3ArtifactSink(bucket=bucket, prefix=prefix)

        # Pre-create the bucket against LocalStack — real AWS buckets
        # must exist ahead of time.
        if os.environ.get("AWS_ENDPOINT_URL"):
            client = await s._ensure_client()
            import botocore.exceptions  # type: ignore[import-untyped]

            try:
                client.create_bucket(Bucket=bucket)
            except botocore.exceptions.ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                    raise
        try:
            yield s
        finally:
            # Best-effort cleanup: delete keys under the test prefix.
            try:
                for k in await s.list(""):
                    await s.delete(k)
            finally:
                await s.close()
