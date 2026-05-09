"""Keep-failed-actions context formatter (Manus harness pattern #5).

Instead of scrubbing tool-call failures during compaction, tag them with a
``[FAILED -- kept for reference]`` prefix and let them age out via a
recency-weight half-life. Manus published this as the implicit-learning lesson
from their five harness rewrites: deleting failures forces the model to
re-derive the failure mode each time it sees a similar tool call, while
keeping them lets the model learn to avoid the same pitfall.

This module is pure-string transformation; it doesn't depend on the rest of
the compaction pipeline. The pipeline calls
:func:`tag_failed_actions` in its pre-compact stage when the
:class:`HarnessPolicy.keep_failed_actions` flag is on, and the deterministic
summary path (:func:`summarize_context`) preserves the tagged blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Marker line prefixed to retained failed-action blocks. Two ASCII hyphens
#: are used (not an em-dash) to keep diff/grep behaviour predictable across
#: encodings.
RETAINED_PREFIX: str = "[FAILED -- kept for reference]"

#: Default half-life (in turns) before a retained failure is considered
#: stale. After ``half_life_turns`` turns the formatter writes a
#: ``staleness=N`` annotation; downstream compactors may use it as an
#: eviction signal once the model has clearly moved past the failure.
DEFAULT_HALF_LIFE_TURNS: int = 6


@dataclass(frozen=True, slots=True)
class FailedActionBlock:
    """A single tool-call failure carved out of agent scrollback.

    Attributes:
        tool_name: Name of the tool that failed (e.g. ``run_tests``).
        error_text: Verbatim error / traceback / stderr captured from the
            adapter. Empty string when the failure surfaced as just a
            non-zero exit code with no stderr.
        turn_index: Which turn (0-based) the failure occurred on. Used to
            compute staleness when the formatter is asked.
    """

    tool_name: str
    error_text: str
    turn_index: int = 0


def tag_failed_actions(
    blocks: Sequence[FailedActionBlock],
    *,
    current_turn: int = 0,
    half_life_turns: int = DEFAULT_HALF_LIFE_TURNS,
) -> str:
    """Render retained failure blocks as a single context-ready string.

    Args:
        blocks: Failure blocks to retain, oldest first.
        current_turn: Index of the turn currently being assembled. Used to
            tag staleness on each block.
        half_life_turns: After this many turns past the failure, mark the
            block as ``staleness=N`` so a downstream compactor can drop it.

    Returns:
        A newline-joined string suitable for inlining into the prompt's
        scrollback. Empty string when *blocks* is empty.
    """
    if not blocks:
        return ""
    if half_life_turns <= 0:
        raise ValueError("half_life_turns must be positive")

    rendered: list[str] = []
    for block in blocks:
        age = max(0, current_turn - block.turn_index)
        staleness = age // half_life_turns
        header = f"{RETAINED_PREFIX} tool={block.tool_name} turn={block.turn_index} staleness={staleness}"
        body = block.error_text.strip() or "(no stderr captured)"
        rendered.append(f"{header}\n{body}")
    return "\n\n".join(rendered)


_FAILED_BLOCK_RE = re.compile(
    rf"{re.escape(RETAINED_PREFIX)}[^\n]*\n.*?(?=(?:\n\n)|\Z)",
    re.DOTALL,
)


def split_retained_blocks(context_text: str) -> tuple[str, list[str]]:
    """Extract retained failure blocks from a context string.

    Useful for compactors that want to summarise the non-retained scrollback
    aggressively while preserving retained failures verbatim.

    Args:
        context_text: Full scrollback / context string.

    Returns:
        A two-tuple ``(non_retained, retained_blocks)``. The first element is
        the input with retained blocks removed (and surrounding whitespace
        normalised); the second is the list of retained block strings in
        original order.
    """
    retained = _FAILED_BLOCK_RE.findall(context_text)
    non_retained = _FAILED_BLOCK_RE.sub("", context_text)
    # Collapse the double-blank-lines that removal leaves behind.
    non_retained = re.sub(r"\n{3,}", "\n\n", non_retained).strip()
    return non_retained, retained


def filter_stale_blocks(
    blocks: Sequence[FailedActionBlock],
    *,
    current_turn: int,
    half_life_turns: int = DEFAULT_HALF_LIFE_TURNS,
    max_staleness: int = 2,
) -> list[FailedActionBlock]:
    """Drop blocks whose computed staleness exceeds *max_staleness*.

    Args:
        blocks: Failure blocks, oldest first.
        current_turn: Turn currently being assembled.
        half_life_turns: Half-life used in staleness computation.
        max_staleness: Maximum staleness (inclusive) to keep. ``0`` keeps only
            failures from the most recent ``half_life_turns`` window;
            ``2`` keeps three half-life windows.

    Returns:
        Filtered list with the same ordering; blocks with
        ``(current_turn - turn_index) // half_life_turns > max_staleness``
        are dropped.
    """
    if half_life_turns <= 0:
        raise ValueError("half_life_turns must be positive")
    if max_staleness < 0:
        raise ValueError("max_staleness must be non-negative")
    out: list[FailedActionBlock] = []
    for block in blocks:
        age = max(0, current_turn - block.turn_index)
        if age // half_life_turns <= max_staleness:
            out.append(block)
    return out


def block_from_dict(payload: dict[str, Any]) -> FailedActionBlock:
    """Build a :class:`FailedActionBlock` from a dict payload.

    Convenience for adapters that emit failures as plain dicts on a queue.

    Args:
        payload: Mapping with at least ``tool_name`` and ``error_text`` keys.
            ``turn_index`` defaults to ``0`` when absent.

    Returns:
        A :class:`FailedActionBlock`.

    Raises:
        KeyError: If ``tool_name`` or ``error_text`` is missing.
    """
    return FailedActionBlock(
        tool_name=str(payload["tool_name"]),
        error_text=str(payload["error_text"]),
        turn_index=int(payload.get("turn_index", 0)),
    )


__all__ = [
    "DEFAULT_HALF_LIFE_TURNS",
    "RETAINED_PREFIX",
    "FailedActionBlock",
    "block_from_dict",
    "filter_stale_blocks",
    "split_retained_blocks",
    "tag_failed_actions",
]
