"""First-divergence locator for replay event logs.

Walks two ``events.jsonl`` files line-by-line and reports the first index
at which the recorded responses diverge. Used by
``bernstein replay diff <run_a> <run_b>`` to pinpoint *where* two runs
behaved differently.

Comparison is intentionally simple — equality on the ``(kind, key,
response)`` triple. Timestamps and metadata are ignored because they
vary by wall-clock even on identical runs. Callers who want stricter
matching can compose the dataclass with their own comparator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class DivergenceResult:
    """Outcome of comparing two event logs.

    Attributes:
        diverged: ``True`` if any divergence was found (including length
            mismatch).
        index: 0-based position of the first divergent event, or ``None``
            if the logs are identical *and* of equal length.
        reason: Short human-readable explanation of the divergence.
        a_event: The event from ``run_a`` at :attr:`index` (or ``None``
            if ``run_a`` ran out first).
        b_event: The event from ``run_b`` at :attr:`index` (or ``None``
            if ``run_b`` ran out first).
    """

    diverged: bool
    index: int | None
    reason: str
    a_event: dict[str, Any] | None = None
    b_event: dict[str, Any] | None = None


def load_events(path: Path) -> list[dict[str, Any]]:
    """Load and parse an ``events.jsonl`` file.

    Args:
        path: Path to the file.

    Returns:
        Parsed event dicts in file order. Malformed lines are skipped.
    """
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open() as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return events


def _comparable(event: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Project an event onto the fields used for divergence comparison."""
    return (
        event.get("kind"),
        event.get("key"),
        event.get("response"),
    )


def diff_event_logs(path_a: Path, path_b: Path) -> DivergenceResult:
    """Return the first index at which two event logs diverge.

    Args:
        path_a: Path to the first ``events.jsonl``.
        path_b: Path to the second ``events.jsonl``.

    Returns:
        A :class:`DivergenceResult` describing the outcome.
    """
    a = load_events(path_a)
    b = load_events(path_b)

    if not a and not b:
        return DivergenceResult(
            diverged=False,
            index=None,
            reason="both event logs are empty",
        )

    limit = min(len(a), len(b))
    for i in range(limit):
        if _comparable(a[i]) != _comparable(b[i]):
            return DivergenceResult(
                diverged=True,
                index=i,
                reason=(f"event #{i} differs: kind/key/response triple does not match"),
                a_event=a[i],
                b_event=b[i],
            )

    if len(a) == len(b):
        return DivergenceResult(
            diverged=False,
            index=None,
            reason=f"identical: {len(a)} events match",
        )

    longer = "a" if len(a) > len(b) else "b"
    return DivergenceResult(
        diverged=True,
        index=limit,
        reason=(f"run_{longer} has {abs(len(a) - len(b))} extra event(s) after index {limit - 1 if limit else 0}"),
        a_event=a[limit] if limit < len(a) else None,
        b_event=b[limit] if limit < len(b) else None,
    )
