"""Tests for the cloud-mount entries added to WorkspaceManifest (oai-003)."""

from __future__ import annotations

from bernstein.core.sandbox import (
    AzureBlobMount,
    GCSMount,
    R2Mount,
    S3Mount,
    WorkspaceManifest,
)


def test_manifest_default_has_no_artifact_mounts() -> None:
    manifest = WorkspaceManifest()
    assert manifest.artifact_mounts == ()


def test_manifest_accepts_s3_mount() -> None:
    mount = S3Mount(
        bucket="my-bucket",
        prefix="runs/abc",
        mount_path="/workspace/.sdd",
        region="us-east-1",
        credentials_env=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    )
    manifest = WorkspaceManifest(artifact_mounts=(mount,))
    assert manifest.artifact_mounts == (mount,)


def test_manifest_accepts_mixed_cloud_mounts() -> None:
    gcs = GCSMount(
        bucket="my-gcs",
        prefix="runs/abc",
        mount_path="/workspace/.sdd",
    )
    azure = AzureBlobMount(
        container="my-container",
        prefix="runs/abc",
        mount_path="/workspace/.azure",
        account_name="myaccount",
    )
    r2 = R2Mount(
        bucket="my-r2",
        prefix="runs/abc",
        mount_path="/workspace/.r2",
        account_id="acc-xyz",
    )
    manifest = WorkspaceManifest(artifact_mounts=(gcs, azure, r2))
    assert len(manifest.artifact_mounts) == 3


def test_s3_mount_is_frozen() -> None:
    import dataclasses

    mount = S3Mount(bucket="b", prefix="p", mount_path="/m")
    # Attempting to mutate must raise FrozenInstanceError
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        mount.bucket = "other"  # type: ignore[misc]


def test_s3_mount_has_sensible_defaults() -> None:
    mount = S3Mount(bucket="b", prefix="", mount_path="/m")
    assert mount.region is None
    assert mount.endpoint_url is None
    assert mount.credentials_env == ()
    assert mount.read_only is False


def test_r2_mount_requires_account_id() -> None:
    """R2Mount.account_id has no default — enforced by dataclass signature."""
    import inspect

    sig = inspect.signature(R2Mount)
    params = sig.parameters
    # account_id must appear without a default
    assert params["account_id"].default is inspect.Parameter.empty
