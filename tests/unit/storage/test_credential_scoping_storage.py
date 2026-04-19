"""Verify sink credentials never leak into agent environments (oai-003)."""

from __future__ import annotations

from bernstein.core.storage.credential_scoping import (
    STORAGE_CREDENTIAL_ENV_VARS,
    list_storage_credential_env_vars,
    scrub_env,
)


def test_storage_credential_env_vars_covers_expected_providers() -> None:
    """All four providers must have their core env vars listed."""
    # S3
    assert "AWS_ACCESS_KEY_ID" in STORAGE_CREDENTIAL_ENV_VARS
    assert "AWS_SECRET_ACCESS_KEY" in STORAGE_CREDENTIAL_ENV_VARS
    # GCS
    assert "GOOGLE_APPLICATION_CREDENTIALS" in STORAGE_CREDENTIAL_ENV_VARS
    # Azure
    assert "AZURE_STORAGE_CONNECTION_STRING" in STORAGE_CREDENTIAL_ENV_VARS
    # R2
    assert "R2_ACCOUNT_ID" in STORAGE_CREDENTIAL_ENV_VARS
    assert "R2_ACCESS_KEY_ID" in STORAGE_CREDENTIAL_ENV_VARS
    assert "R2_SECRET_ACCESS_KEY" in STORAGE_CREDENTIAL_ENV_VARS


def test_list_sorted_and_complete() -> None:
    listed = list_storage_credential_env_vars()
    assert listed == sorted(listed)
    assert set(listed) == STORAGE_CREDENTIAL_ENV_VARS


def test_scrub_env_removes_all_listed_keys() -> None:
    env = {
        "PATH": "/usr/bin",
        "AWS_ACCESS_KEY_ID": "AKIA...",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "GOOGLE_APPLICATION_CREDENTIALS": "/creds.json",
        "AZURE_STORAGE_CONNECTION_STRING": "Default...",
        "R2_ACCOUNT_ID": "acc",
        "HOME": "/home/agent",
    }
    scrubbed = scrub_env(env)
    assert "AWS_ACCESS_KEY_ID" not in scrubbed
    assert "AWS_SECRET_ACCESS_KEY" not in scrubbed
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in scrubbed
    assert "AZURE_STORAGE_CONNECTION_STRING" not in scrubbed
    assert "R2_ACCOUNT_ID" not in scrubbed
    # Non-sink env vars preserved
    assert scrubbed["PATH"] == "/usr/bin"
    assert scrubbed["HOME"] == "/home/agent"


def test_scrub_env_does_not_mutate_input() -> None:
    env = {"AWS_ACCESS_KEY_ID": "k", "PATH": "/bin"}
    _ = scrub_env(env)
    assert env == {"AWS_ACCESS_KEY_ID": "k", "PATH": "/bin"}


def test_scrub_env_on_empty_dict() -> None:
    assert scrub_env({}) == {}
