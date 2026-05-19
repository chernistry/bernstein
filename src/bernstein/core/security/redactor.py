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

    from bernstein.core.security.redactor import redact_text, redact_file, mask

    cleaned = redact_text(raw)
    cleaned, count = redact_file(Path("bernstein.yaml"))
    safe = mask(api_key)  # short-value helper for logger.info("got %s", mask(token))
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from bernstein.core.observability.debug_bundle import redact_secrets

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["collapse_home", "mask", "redact_file", "redact_text"]

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
    cleaned, broker_count = _scrub_broker_registry(cleaned)
    cleaned = collapse_home(cleaned)
    return cleaned, count + broker_count


def _scrub_broker_registry(text: str) -> tuple[str, int]:
    """Replace any value registered with the secrets broker with ``***``.

    The registry is consulted lazily and tolerantly: import failures or an
    empty registry are no-ops so this function never breaks the broader
    redaction pipeline.
    """
    try:
        from bernstein.core.security.secrets_broker import get_redactable_values
    except Exception:
        return text, 0
    values = get_redactable_values()
    if not values:
        return text, 0
    out = text
    count = 0
    # Replace longest values first so prefixes never mask longer matches.
    for value in sorted(values, key=len, reverse=True):
        if value and value in out:
            count += out.count(value)
            out = out.replace(value, "***")
    return out, count


def mask(value: Any, *, keep: int = 0) -> str:
    """Mask an individual short value for safe inclusion in a log line.

    Use this for credential-shaped scalars (API keys, bearer tokens, OAuth
    response bodies, JWT signatures) where the file-level
    :func:`redact_text` pipeline is overkill but you still want a
    one-shot, hard-to-misuse helper at the call site::

        logger.info("token issued: %s", mask(token))
        logger.error("OAuth failed: %s %s", status, mask(resp.text))

    Args:
        value: Anything stringifiable. ``None`` becomes ``"<none>"`` so
            log lines stay scannable without leaking type info.
        keep: Number of trailing characters to keep visible (default
            ``0``). Use sparingly for correlation; values above ``4``
            risk re-exposing short secrets and are clamped.

    Returns:
        A redacted string of the form ``"***"`` (default) or
        ``"***abcd"`` when ``keep > 0`` AND the input is longer than
        ``keep`` characters. Inputs that are ``<= keep`` characters
        long are fully masked to ``"***"`` rather than exposing their
        entire value (otherwise a 4-character secret with ``keep=4``
        would be printed verbatim). Empty strings render as
        ``"<empty>"`` so a missing secret is visually distinct from a
        masked one.
    """
    if value is None:
        return "<none>"
    text = str(value)
    if not text:
        return "<empty>"
    keep = max(0, min(keep, 4))
    if keep == 0 or len(text) <= keep:
        return "***"
    return f"***{text[-keep:]}"


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
