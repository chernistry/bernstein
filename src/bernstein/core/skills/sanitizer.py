"""Sanitize invisible Unicode codepoints from skill bodies before injection.

Public research (Feb 2026, "Scary Agent Skills: Hidden Unicode Instructions in
Skills" by Embrace the Red; Snyk skill-pack audit of 3,984 public files showing
36.82% with security flaws) demonstrated that invisible Unicode Tag codepoints
in the ``U+E0000-U+E007F`` range are interpreted as instructions by Claude,
Gemini, and Grok models. A poisoned third-party skill, once written into
``.claude/skills/*.md`` in an agent worktree, would be silently embedded into
every spawn that triggers it.

This module strips every codepoint whose Unicode category is ``Cf`` (format),
every codepoint in the Tag block (``U+E0000-U+E007F``), and the interlinear
annotation marks (``U+FFF9-U+FFFB``). The function returns the cleaned text
along with the count of stripped codepoints so callers can emit telemetry.

Defaults:
    Sanitization is **ON** by default. The opt-out path is the
    ``BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS`` environment variable (set by the
    hidden ``--unsafe-allow-unicode-tags`` CLI flag). Opting out is dangerous
    and should only be used for reproducing a poisoned-skill incident in a
    controlled environment.

The implementation is pure stdlib (``unicodedata.category``); no new deps.
"""

from __future__ import annotations

import logging
import os
import unicodedata

logger = logging.getLogger(__name__)

# Tag block (deprecated by Unicode but still rendered as zero-width by most
# fonts). Every codepoint here is invisible and exploitable.
_TAG_BLOCK_START: int = 0xE0000
_TAG_BLOCK_END: int = 0xE007F  # inclusive

# Interlinear annotation marks: anchor/separator/terminator. Not in ``Cf`` on
# every Python release, so we list them explicitly to be safe.
_INTERLINEAR_START: int = 0xFFF9
_INTERLINEAR_END: int = 0xFFFB  # inclusive

# Environment-variable opt-out. The CLI sets this before any skill load so the
# loader sees a consistent answer.
_OPT_OUT_ENV: str = "BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS"


def _is_invisible_tag(codepoint: str) -> bool:
    """Return ``True`` if *codepoint* is an invisible or format-class glyph.

    Stripped categories:

    - Unicode category ``Cf`` (format).
    - Range ``U+E0000-U+E007F`` (Tag block).
    - Range ``U+FFF9-U+FFFB`` (interlinear annotation marks).

    Args:
        codepoint: A single-character string.

    Returns:
        ``True`` if the codepoint matches any rule above.
    """
    if len(codepoint) != 1:
        # Defensive: callers always pass single chars but guard against
        # surrogate pairs or multi-char graphemes anyway.
        return False
    ord_value = ord(codepoint)
    if _TAG_BLOCK_START <= ord_value <= _TAG_BLOCK_END:
        return True
    if _INTERLINEAR_START <= ord_value <= _INTERLINEAR_END:
        return True
    return unicodedata.category(codepoint) == "Cf"


def is_sanitization_enabled() -> bool:
    """Return whether sanitization is enabled in the current process.

    Looks at the ``BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS`` env var. When the
    variable is set to ``"1"``, ``"true"``, ``"yes"``, or ``"on"`` (case
    insensitive), sanitization is **disabled** and skill bodies pass through
    untouched. Default is ``True`` (sanitize).
    """
    raw = os.environ.get(_OPT_OUT_ENV, "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def strip_invisible_tags(text: str) -> tuple[str, int]:
    """Strip invisible Unicode codepoints from *text*.

    The function never extends *text*; it only removes characters. The return
    is a tuple of ``(cleaned, count)`` where ``count`` is the number of
    codepoints removed. ``count == 0`` means *text* was already safe and the
    cleaned string is equal to the original.

    Args:
        text: Arbitrary user-supplied string (usually a skill body).

    Returns:
        Tuple ``(cleaned, count)``.

    Example:
        >>> strip_invisible_tags("hello")
        ('hello', 0)
        >>> payload = "\\U000e0048\\U000e0045\\U000e004c\\U000e004c\\U000e004f"
        >>> strip_invisible_tags(payload)
        ('', 5)
    """
    if not text:
        return ("", 0)

    out: list[str] = []
    count = 0
    for ch in text:
        if _is_invisible_tag(ch):
            count += 1
            continue
        out.append(ch)
    if count == 0:
        # Fast path: avoid re-joining when nothing changed so the identity
        # property is preserved.
        return (text, 0)
    return ("".join(out), count)


def sanitize_skill_body(
    body: str,
    *,
    skill_name: str,
    origin: str,
    source_name: str,
) -> str:
    """Sanitize *body* and emit a WARN log + Prometheus counter on hits.

    Wraps :func:`strip_invisible_tags` with the orchestrator-side wiring so the
    loader does not need to know how to talk to the observability subsystem.
    Honours the ``--unsafe-allow-unicode-tags`` opt-out: when sanitization is
    disabled, *body* is returned verbatim and no telemetry is emitted.

    Args:
        body: Raw skill body text.
        skill_name: Skill name, included in the WARN log line.
        origin: Where the skill came from (filesystem path or plugin label).
            Included in the WARN log line.
        source_name: Label of the owning :class:`SkillSource`. Used as the
            Prometheus counter label.

    Returns:
        Sanitized body, or *body* unchanged when sanitization is disabled
        or no invisible codepoints were found.
    """
    if not is_sanitization_enabled():
        return body

    cleaned, count = strip_invisible_tags(body)
    if count > 0:
        logger.warning(
            "skills.unicode_tags_stripped name=%r origin=%r source=%r count=%d",
            skill_name,
            origin,
            source_name,
            count,
        )
        _record_counter(source_name, count)
    return cleaned


def _record_counter(source_name: str, count: int) -> None:
    """Increment the Prometheus counter, swallowing import errors.

    The Prometheus module has a stub fallback when ``prometheus_client`` is
    unavailable, but we still wrap the import in a try/except in case the
    observability subsystem cannot be loaded at all (e.g. partial wheel).
    """
    try:
        from bernstein.core.observability.prometheus import (
            skills_unicode_tags_stripped_total,
        )

        skills_unicode_tags_stripped_total.labels(source_name=source_name).inc(count)  # type: ignore[no-untyped-call]
    except Exception:  # pragma: no cover - defensive
        logger.debug("Failed to record skills_unicode_tags_stripped_total", exc_info=True)


__all__ = [
    "is_sanitization_enabled",
    "sanitize_skill_body",
    "strip_invisible_tags",
]
