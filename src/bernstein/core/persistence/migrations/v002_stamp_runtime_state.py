"""Stamp an explicit ``schema_version`` into runtime state JSON files.

Older ``.sdd/runtime/*.json`` state files were written without an explicit
``schema_version`` key; readers inferred "version 1" from its absence. That
inference is exactly the kind of inline compat branch this package exists to
retire. This migration walks the runtime JSON files once and stamps the key
so future readers can branch on it unconditionally.

Forward-only and idempotent: a file that already carries ``schema_version``
is left untouched, and re-running the migration over a stamped tree changes
nothing.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from bernstein.core.persistence.atomic_write import write_atomic_json

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

VERSION = 2
DESCRIPTION = "stamp schema_version into runtime state json"

# Runtime state files that this migration stamps. Kept explicit rather than a
# blanket ``*.json`` glob so we never rewrite append-only logs or files owned
# by other subsystems.
_RUNTIME_STATE_FILES = (
    "supervisor_state.json",
    "config_state.json",
)

_STAMPED_VERSION = 1
"""The shape these files already had is, by definition, version 1."""


def apply(state_dir: Path) -> None:
    """Stamp ``schema_version`` into known runtime state JSON files.

    Args:
        state_dir: The ``.sdd`` state directory.
    """
    runtime_dir = state_dir / "runtime"
    if not runtime_dir.is_dir():
        return
    for name in _RUNTIME_STATE_FILES:
        path = runtime_dir / name
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("skipping unparseable runtime state %s: %s", path, exc)
            continue
        if not isinstance(raw, dict) or "schema_version" in raw:
            continue
        raw["schema_version"] = _STAMPED_VERSION
        write_atomic_json(path, raw)
        logger.info("stamped schema_version into %s", path.name)


def down(state_dir: Path) -> None:
    """Remove the stamped ``schema_version`` key from runtime state files."""
    runtime_dir = state_dir / "runtime"
    if not runtime_dir.is_dir():
        return
    for name in _RUNTIME_STATE_FILES:
        path = runtime_dir / name
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and raw.pop("schema_version", None) is not None:
            write_atomic_json(path, raw)
