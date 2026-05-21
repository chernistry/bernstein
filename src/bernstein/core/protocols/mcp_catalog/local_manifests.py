"""Bundled-with-Bernstein local manifest loader.

The remote catalog at ``https://bernstein.run/mcp-catalog.json`` is the
primary source of installable MCP servers, but a curated handful ships
inside the wheel under :mod:`bernstein.core.protocols.mcp_catalog.manifests`
so operators can discover and opt into recommended servers without a
network round-trip. Each YAML file in that directory parses through the
same :func:`~bernstein.core.protocols.mcp_catalog.manifest.validate_catalog`
strict validator the remote payload uses, so the schema cannot drift.

Local entries are *available, disabled by default* - the
``mcp.catalog.<entry_id>.enabled`` config flag (see
:mod:`bernstein.core.defaults`) gates whether the entry is registered with
the live MCP server set. Until enabled, ``bernstein mcp catalog list`` shows
them as ``disabled``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from bernstein.core.protocols.mcp_catalog.manifest import (
    Catalog,
    CatalogEntry,
    CatalogValidationError,
    validate_catalog,
)

#: Directory that ships local manifests inside the wheel.
LOCAL_MANIFESTS_DIR: Path = Path(__file__).parent / "manifests"

#: Schema version consumed by :func:`validate_catalog`.
_LOCAL_CATALOG_SCHEMA_VERSION = 1


def _coerce_entry_payload(raw: object, source: Path) -> dict[str, Any]:
    """Validate that a parsed YAML body is an entry-shaped dict.

    The ``Any`` value type is intentional: per-field strict validation
    happens inside :func:`validate_catalog`, not here.
    """
    if not isinstance(raw, dict):
        raise CatalogValidationError(f"local manifest {source.name!r} must be a YAML mapping, got {type(raw).__name__}")
    out: dict[str, Any] = {}
    for key, value in raw.items():  # type: ignore[reportUnknownVariableType]
        if not isinstance(key, str):
            raise CatalogValidationError(f"local manifest {source.name!r} has non-string key {key!r}")
        out[key] = value
    return out


def _read_manifest_file(path: Path) -> dict[str, Any]:
    """Parse a single ``*.yaml`` manifest into a raw dict."""
    body: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    return _coerce_entry_payload(body, path)


def load_local_manifests(directory: Path | None = None) -> Catalog:
    """Load every ``*.yaml`` manifest under ``directory`` as a :class:`Catalog`.

    Entries are sorted by ``id`` so the result is deterministic across
    operating systems and filesystem orderings.

    Args:
        directory: Override the default manifests directory. ``None`` uses
            :data:`LOCAL_MANIFESTS_DIR`.

    Returns:
        A validated :class:`Catalog` whose ``entries`` are the parsed
        local manifests. Empty when no ``*.yaml`` files are present.

    Raises:
        CatalogValidationError: If any manifest fails strict validation.
            The whole load is rejected (matching remote-fetch semantics).
    """
    root = directory or LOCAL_MANIFESTS_DIR
    raw_entries: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.glob("*.yaml")):
            raw_entries.append(_read_manifest_file(path))

    payload: dict[str, Any] = {
        "version": _LOCAL_CATALOG_SCHEMA_VERSION,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "entries": raw_entries,
    }
    return validate_catalog(payload)


def find_local_entry(entry_id: str, *, directory: Path | None = None) -> CatalogEntry | None:
    """Look up a single local manifest entry by id."""
    return load_local_manifests(directory).find(entry_id)


__all__ = [
    "LOCAL_MANIFESTS_DIR",
    "find_local_entry",
    "load_local_manifests",
]
