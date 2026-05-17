"""Anonymous install identifier with lazy, opt-in-gated generation.

The install id is a 128-bit UUID v4 written once to
``~/.bernstein/install-id`` and only generated AFTER an explicit opt-in.
Before opt-in this module's load and read paths must never produce a new
id.  ``ensure(...)`` is the single mutation entry point; it raises if
called while telemetry is disabled.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from typing import TYPE_CHECKING

from bernstein.core.telemetry.config import install_id_path, is_enabled

if TYPE_CHECKING:
    from pathlib import Path


def read(home: Path | None = None) -> str | None:
    """Return the persisted id, or ``None`` if the file is missing/empty."""
    path = install_id_path(home)
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def ensure(home: Path | None = None) -> str:
    """Return the install id, generating one if needed.

    Raises:
        RuntimeError: if telemetry is not currently enabled.  This is the
            critical invariant: the id must never be created before opt-in.
    """
    if not is_enabled(home=home):
        raise RuntimeError("install id may not be generated before explicit opt-in")
    existing = read(home)
    if existing is not None:
        return existing
    new_id = uuid.uuid4().hex
    path = install_id_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict to operator only.  Best-effort on platforms that honour mode.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(new_id + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return new_id


def reset(home: Path | None = None) -> None:
    """Delete the install id.  Called from ``bernstein telemetry off``."""
    path = install_id_path(home)
    try:
        path.unlink()
    except FileNotFoundError:
        return


__all__ = ["ensure", "read", "reset"]
