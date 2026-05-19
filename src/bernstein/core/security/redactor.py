"""File-level redaction wrapper for debug-bundle text artefacts.

This is a thin orchestration layer over the lower-level pattern set in
:mod:`bernstein.core.observability.debug_bundle`. It exposes a stable,
text-only API that:

- Blanks API keys, tokens, secrets, passwords, bearer headers, JWTs, SSH
  keys, and URL-embedded credentials.
- Collapses absolute paths under ``$HOME`` to ``~``.
- Removes values of environment variables whose names contain
  ``KEY``/``TOKEN``/``SECRET``/``PASSWORD`` from text-style dumps
  (``NAME=value`` and ``NAME: value``).

The wrapper is intentionally text-only and idempotent so callers can
feed it any UTF-8 file content without bespoke parsing.

Usage::

    from bernstein.core.security.redactor import redact_text, redact_file

    cleaned = redact_text(raw)
    cleaned, count = redact_file(Path("bernstein.yaml"))
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from bernstein.core.observability.debug_bundle import redact_secrets

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["collapse_home", "redact_file", "redact_text"]

_HOME_RE: re.Pattern[str] | None = None


def _home_pattern() -> re.Pattern[str] | None:
    """Return a compiled regex matching the current ``$HOME`` prefix.

    Compiled lazily because ``$HOME`` can be unset in CI runners; we
    return ``None`` in that case and skip the collapse step.
    """
    global _HOME_RE
    if _HOME_RE is not None:
        return _HOME_RE
    home = os.environ.get("HOME") or os.path.expanduser("~")
    if not home or home == "~":
        return None
    # Match the literal home path as a path prefix.
    _HOME_RE = re.compile(re.escape(home.rstrip("/")))
    return _HOME_RE


def collapse_home(text: str) -> str:
    """Replace occurrences of ``$HOME`` with ``~`` in *text*."""
    pattern = _home_pattern()
    if pattern is None:
        return text
    return pattern.sub("~", text)


def redact_text(text: str) -> tuple[str, int]:
    """Redact secrets and collapse ``$HOME`` references in *text*.

    Args:
        text: Arbitrary UTF-8 string that may contain secrets or
            absolute paths.

    Returns:
        A 2-tuple of (redacted text, count of secret redactions
        applied). The home-collapse step is not counted because it is
        cosmetic, not a security action.
    """
    cleaned, count = redact_secrets(text)
    cleaned = collapse_home(cleaned)
    return cleaned, count


def redact_file(path: Path) -> tuple[str, int]:
    """Read *path* as UTF-8 and run :func:`redact_text` over its body.

    Args:
        path: File to read. Missing or unreadable files yield an empty
            string and zero redactions; callers decide how to surface
            the absence.

    Returns:
        A 2-tuple of (redacted text, count of secret redactions).
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0
    return redact_text(raw)
