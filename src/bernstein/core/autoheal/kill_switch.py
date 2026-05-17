"""File-backed kill switch for auto-heal.

The workflow checks ``.sdd/autoheal-disabled`` first thing in every
run. The file's content is one of:

* empty / missing -> auto-heal enabled
* ``"forever"`` -> disabled indefinitely
* ISO-8601 timestamp -> disabled until that time

The companion CLI surface is::

    bernstein autoheal disable --until 2026-05-20
    bernstein autoheal disable --forever
    bernstein autoheal enable

This module implements the read side that the workflow uses; the CLI
write side is wired separately (out of scope for v2 wave one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    """Resolved kill-switch decision and rationale."""

    disabled: bool
    reason: str


def read(path: Path, *, now: datetime | None = None) -> KillSwitchState:
    """Resolve the current kill-switch state.

    Args:
        path: Path to ``.sdd/autoheal-disabled`` (or test stand-in).
        now: Optional injected clock for deterministic tests; defaults
            to ``datetime.now(timezone.utc)``.

    Returns:
        A :class:`KillSwitchState`. ``disabled`` is ``False`` when the
        file is missing, empty, or carries an already-expired ISO time.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return KillSwitchState(disabled=False, reason="no_file")

    if not text:
        return KillSwitchState(disabled=False, reason="empty_file")

    if text.lower() == "forever":
        return KillSwitchState(disabled=True, reason="forever")

    parsed = _parse_iso(text)
    if parsed is None:
        # Unrecognised payload -> fail safe (treat as disabled), and
        # surface the reason so the operator can fix it.
        return KillSwitchState(disabled=True, reason=f"unparseable:{text!r}")

    n = now if now is not None else datetime.now(UTC)
    if parsed <= n:
        return KillSwitchState(disabled=False, reason=f"expired_at:{parsed.isoformat()}")
    return KillSwitchState(disabled=True, reason=f"until:{parsed.isoformat()}")


def _parse_iso(text: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, with or without tz; tz-naive becomes UTC."""
    candidate = text
    # Accept the trailing ``Z`` form.
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


__all__ = [
    "KillSwitchState",
    "read",
]
