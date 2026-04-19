"""Integration tests for :class:`AzureBlobArtifactSink` (oai-003).

Gated by:

- ``azure-storage-blob`` must be importable.
- Either Azurite is running on the local default endpoint, or
  ``AZURE_STORAGE_CONNECTION_STRING`` points at a real account.
- ``BERNSTEIN_AZURE_TEST_CONTAINER`` names a test container.

Run locally with::

    docker run -d -p 10000:10000 mcr.microsoft.com/azure-storage/azurite
    AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=http;\
AccountName=devstoreaccount1;\
AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;\
BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;" \\
    BERNSTEIN_AZURE_TEST_CONTAINER=bernstein-it \\
    uv run pytest tests/integration/storage/test_azure_blob_sink.py -x -q
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


def _azure_available() -> bool:
    try:
        if importlib.util.find_spec("azure.storage.blob") is None:
            return False
    except (ImportError, ModuleNotFoundError, ValueError):
        # ``find_spec`` raises when a parent package is missing rather
        # than returning ``None``. Treat that as "not installed".
        return False
    if os.environ.get("BERNSTEIN_SKIP_AZURE_TESTS") == "1":
        return False
    return bool(
        os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        or (os.environ.get("AZURE_STORAGE_ACCOUNT_NAME") and os.environ.get("AZURE_STORAGE_ACCOUNT_KEY")),
    )


pytestmark = pytest.mark.skipif(
    not _azure_available(),
    reason="azure-storage-blob not installed or credentials missing",
)


class TestAzureBlobSinkConformance(ArtifactSinkConformance):
    """Runs the shared conformance suite against a live Azure Blob endpoint."""

    large_payload_bytes = 256 * 1024

    @pytest_asyncio.fixture
    async def sink(self) -> AsyncIterator[ArtifactSink]:
        from bernstein.core.storage.sinks.azure_blob import (
            AzureBlobArtifactSink,
        )

        container = os.environ.get(
            "BERNSTEIN_AZURE_TEST_CONTAINER",
            "bernstein-test",
        )
        prefix = f"it/{uuid.uuid4()}"
        s = AzureBlobArtifactSink(container=container, prefix=prefix)

        # Ensure the container exists (Azurite needs explicit creation).
        import contextlib

        try:
            container_client = await s._ensure_container()
            from azure.core.exceptions import ResourceExistsError  # type: ignore[import-not-found]

            with contextlib.suppress(ResourceExistsError):
                container_client.create_container()
        except Exception:
            # Defer any setup errors to the actual test — some backends
            # don't allow implicit container creation.
            pass
        try:
            yield s
        finally:
            try:
                for k in await s.list(""):
                    await s.delete(k)
            finally:
                await s.close()
