"""Code-provenance scoring for auto-heal.

Newly-introduced lines are safer to touch than old ones. If the
offending line was added in the last 24h, the proposed fix is much
more likely to be a one-character typo than a structural change; a
fix to a line that has been stable for months is far more suspicious.

This module exposes a pure helper that takes a list of (path, line)
hits and a git blame oracle, and returns a per-line provenance score
in ``[0, 1]`` where 1.0 = brand-new, 0.0 = ancient.

The git blame oracle is injected so unit tests can run without a real
repo; the workflow plumbs in a thin git-shelled adapter.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

# Decay window in seconds. Lines newer than this score 1.0; lines older
# than ``HALF_LIFE_SECONDS * 365`` score effectively 0.
HALF_LIFE_SECONDS: float = 24 * 60 * 60
MAX_AGE_SECONDS: float = HALF_LIFE_SECONDS * 365


@dataclass(frozen=True, slots=True)
class BlameInfo:
    """Result of one ``git blame`` lookup."""

    path: str
    line: int
    author_time_epoch: float


@dataclass(frozen=True, slots=True)
class ProvenanceScore:
    """Per-line provenance score."""

    path: str
    line: int
    age_seconds: float
    score: float


BlameOracle = Callable[[str, int], BlameInfo | None]


def score_line(
    path: str,
    line: int,
    blame: BlameOracle,
    *,
    now: float | None = None,
) -> ProvenanceScore:
    """Return a 0..1 freshness score for one (path, line)."""
    info = blame(path, line)
    n = now if now is not None else time.time()
    if info is None:
        return ProvenanceScore(path=path, line=line, age_seconds=MAX_AGE_SECONDS, score=0.0)
    age = max(0.0, n - info.author_time_epoch)
    if age <= HALF_LIFE_SECONDS:
        return ProvenanceScore(path=path, line=line, age_seconds=age, score=1.0)
    # Exponential decay beyond the half-life.
    # score = 0.5 ** (excess_halflives)
    excess = (age - HALF_LIFE_SECONDS) / HALF_LIFE_SECONDS
    score = 0.5 ** (excess + 1.0)
    return ProvenanceScore(path=path, line=line, age_seconds=age, score=max(0.0, min(1.0, score)))


__all__ = [
    "HALF_LIFE_SECONDS",
    "MAX_AGE_SECONDS",
    "BlameInfo",
    "BlameOracle",
    "ProvenanceScore",
    "score_line",
]
