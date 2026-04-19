"""Smoke tests: cloud sink modules must import without optional SDKs.

The ticket mandates that missing SDKs never break ``import`` — only
instantiation against missing deps should fail. These tests exercise
the guarantee by making sure each sink module is importable even when
the extra-specific SDK is absent.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "bernstein.core.storage.sinks.local_fs",
        "bernstein.core.storage.sinks.s3",
        "bernstein.core.storage.sinks.gcs",
        "bernstein.core.storage.sinks.azure_blob",
        "bernstein.core.storage.sinks.r2",
    ],
)
def test_module_imports_without_sdk(module: str) -> None:
    mod = importlib.import_module(module)
    assert mod is not None


def test_missing_boto3_surfaces_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The S3Unavailable error points the user at the right extra."""
    import sys

    monkeypatch.setitem(sys.modules, "boto3", None)  # force ImportError

    from bernstein.core.storage.sinks.s3 import S3Unavailable, _import_boto3

    with pytest.raises(S3Unavailable, match="s3"):
        _import_boto3()


def test_missing_google_cloud_storage_surfaces_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "google.cloud.storage", None)

    from bernstein.core.storage.sinks.gcs import GCSUnavailable, _import_storage

    with pytest.raises(GCSUnavailable, match="gcs"):
        _import_storage()


def test_missing_azure_blob_surfaces_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "azure.storage.blob", None)

    from bernstein.core.storage.sinks.azure_blob import (
        AzureBlobUnavailable,
        _import_blob_sdk,
    )

    with pytest.raises(AzureBlobUnavailable, match="azure"):
        _import_blob_sdk()
