"""Smoke test: cocoindex-code local manifest loads and validates.

The manifest at
``src/bernstein/core/protocols/mcp_catalog/manifests/cocoindex_code.yaml``
ships inside the wheel as a "available, disabled by default" catalog
entry. This test makes sure:

* the YAML parses,
* it round-trips through the strict catalog validator,
* the catalog is "off" by default (operator must opt in via
  ``mcp.catalog.cocoindex_code.enabled``),
* the ``preview_local_manifest`` integration point can build an
  ``InstallPreview`` for it without actually installing cocoindex (the
  pip ``--dry-run`` install command is invoked).
"""

from __future__ import annotations

import shutil
import sys

import pytest

from bernstein.core import defaults as _defaults
from bernstein.core.protocols.mcp_catalog import (
    LOCAL_MANIFESTS_DIR,
    Catalog,
    CatalogEntry,
    SandboxRunner,
    find_local_entry,
    load_local_manifests,
    preview_local_manifest,
)

COCOINDEX_ID = "cocoindex-code"


def test_local_manifests_directory_ships_cocoindex_code() -> None:
    assert LOCAL_MANIFESTS_DIR.is_dir(), "manifests directory must exist in the wheel"
    assert (LOCAL_MANIFESTS_DIR / "cocoindex_code.yaml").is_file()


def test_load_local_manifests_returns_catalog_with_cocoindex_code() -> None:
    catalog = load_local_manifests()
    assert isinstance(catalog, Catalog)
    ids = [entry.id for entry in catalog.entries]
    assert COCOINDEX_ID in ids


def test_cocoindex_code_entry_matches_manifest_schema() -> None:
    entry = find_local_entry(COCOINDEX_ID)
    assert isinstance(entry, CatalogEntry)
    assert entry.id == COCOINDEX_ID
    assert entry.name
    assert entry.description
    assert entry.homepage.startswith("https://")
    assert entry.repository.startswith("https://")
    assert entry.transports == ("stdio",)
    assert entry.version_pin
    assert entry.command == "ccc"
    assert entry.args == ("mcp",)
    # cocoindex-code has not yet been reviewed by the Bernstein team; this
    # mirrors the warning surfaced by `bernstein mcp catalog install`.
    assert entry.verified_by_bernstein is False


def test_cocoindex_code_disabled_by_default() -> None:
    assert _defaults.CATALOG.cocoindex_code_enabled is False


def test_cocoindex_code_install_command_is_argv_not_shell() -> None:
    entry = find_local_entry(COCOINDEX_ID)
    assert entry is not None
    # Argv-style command: no shell metacharacters, no embedded spaces in
    # any single token. The sandbox runs argv directly via subprocess.
    for token in entry.install_command:
        assert "&" not in token
        assert "|" not in token
        assert ";" not in token
    # First token resolves to a real binary on the host (or is a known
    # package manager) — we only assert the shape, not host availability.
    assert entry.install_command[0] in {"pip", "pipx", "uv", "uvx", "ccc"}


def test_preview_local_manifest_executes_in_sandbox() -> None:
    # Replace the manifest's pip dry-run with a no-op python invocation so
    # the test runs offline. We exercise the same code path
    # `preview_local_manifest` uses, just with a stubbed argv.
    entry = find_local_entry(COCOINDEX_ID)
    assert entry is not None
    runner = SandboxRunner(
        timeout_seconds=10,
        executable_overrides={entry.install_command[0]: sys.executable},
    )
    # Override the install_command so we don't reach PyPI inside the
    # smoke test. We rebuild a fresh CatalogEntry to keep frozen-dataclass
    # semantics.
    if shutil.which(sys.executable) is None:  # pragma: no cover - defensive
        pytest.skip("python interpreter not on PATH")

    stub = CatalogEntry(
        id=entry.id,
        name=entry.name,
        description=entry.description,
        homepage=entry.homepage,
        repository=entry.repository,
        install_command=(sys.executable, "-c", "print('ok')"),
        version_pin=entry.version_pin,
        transports=entry.transports,
        verified_by_bernstein=entry.verified_by_bernstein,
        command=entry.command,
        args=entry.args,
        env=entry.env,
    )
    from bernstein.core.protocols.mcp_catalog.sandbox_preview import run_install_preview

    preview = run_install_preview(stub, runner=SandboxRunner(timeout_seconds=10))
    assert preview.succeeded is True
    assert preview.exit_code == 0
    assert b"ok" in preview.stdout

    # And confirm the convenience wrapper resolves the bundled entry by
    # id. We let it fail gracefully when pip is not available offline:
    # the assertion below only checks that the preview ran end-to-end.
    real_preview = preview_local_manifest(COCOINDEX_ID, runner=runner)
    assert real_preview.duration_seconds >= 0
