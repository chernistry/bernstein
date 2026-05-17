"""Classify failing CI job names into auto-heal safety classes.

Pure-Python categorizer. Reads job names and emits a bucketed map of
safe / heuristic / risky / unknown classes. The matcher is exact where
possible and falls back to short prefix rules for ``Test (...)``-style
matrix legs.

This module is the v2 replacement for the standalone
``scripts/auto_heal_categorize.py`` shipped in v1. The classification
rules are identical so v1 history stays comparable; new in v2 is the
return of a structured ``Classification`` object that carries the rule
that matched, which the bandit and audit-log layers consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

SafetyClass = Literal["safe", "heuristic", "risky", "unknown"]


_SAFE_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Lint",
        "Repo hygiene",
        "Dead code (Vulture)",
        "Snapshot tests (syrupy)",
        "Workflow lint",
    }
)

_HEURISTIC_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Spelling (typos)",
    }
)

_RISKY_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Type check",
        "Pyright strict (security + cluster)",
        "CodeQL",
        "CodeQL (python)",
        "Bandit (security)",
        "Semgrep (custom rules)",
        "Schemathesis smoke",
        "Mutation (diff-only)",
        "Property tests (Hypothesis smoke)",
        "Beartype (type contracts)",
        "Adapter integration (fake-CLI)",
        "Diff coverage gate",
        "pip-audit (deps)",
        "Package size check",
        "Lineage Gate",
        "Determine changes",
        "CI gate",
        "Auto-fix lint",
        "PR CI summary",
        "Close resolved CI issues",
    }
)

_RISKY_PREFIXES: Final[tuple[str, ...]] = ("Test (",)


@dataclass(frozen=True, slots=True)
class Classification:
    """Result of classifying one job name.

    ``rule`` records which matcher fired (``"safe_exact"``,
    ``"heuristic_exact"``, ``"risky_exact"``, ``"risky_prefix"``, or
    ``"unknown_default"``). Used by audit log + bandit reward attribution.
    """

    name: str
    cls: SafetyClass
    rule: str


def classify(job_name: str) -> Classification:
    """Return the safety class for one CI job name.

    Empty / whitespace-only names map to ``unknown``.
    """
    name = job_name.strip()
    if not name:
        return Classification(name=job_name, cls="unknown", rule="unknown_default")
    if name in _SAFE_EXACT:
        return Classification(name=name, cls="safe", rule="safe_exact")
    if name in _HEURISTIC_EXACT:
        return Classification(name=name, cls="heuristic", rule="heuristic_exact")
    if name in _RISKY_EXACT:
        return Classification(name=name, cls="risky", rule="risky_exact")
    for prefix in _RISKY_PREFIXES:
        if name.startswith(prefix):
            return Classification(name=name, cls="risky", rule="risky_prefix")
    return Classification(name=name, cls="unknown", rule="unknown_default")


@dataclass(frozen=True, slots=True)
class BucketedJobs:
    """All four buckets, one job-name list each. Order is preserved."""

    safe: tuple[str, ...]
    heuristic: tuple[str, ...]
    risky: tuple[str, ...]
    unknown: tuple[str, ...]

    def should_heal(self) -> bool:
        """True iff there is at least one safe or heuristic job to act on."""
        return bool(self.safe or self.heuristic)


def bucketize(job_names: Iterable[str]) -> BucketedJobs:
    """Classify many job names and group them into buckets.

    Duplicates in the input are preserved in the bucket order so
    downstream consumers can correlate with the original failing-job
    list 1:1.
    """
    safe: list[str] = []
    heur: list[str] = []
    risky: list[str] = []
    unk: list[str] = []
    for raw in job_names:
        c = classify(raw)
        if c.cls == "safe":
            safe.append(c.name)
        elif c.cls == "heuristic":
            heur.append(c.name)
        elif c.cls == "risky":
            risky.append(c.name)
        else:
            unk.append(c.name)
    return BucketedJobs(
        safe=tuple(safe),
        heuristic=tuple(heur),
        risky=tuple(risky),
        unknown=tuple(unk),
    )


__all__ = [
    "BucketedJobs",
    "Classification",
    "SafetyClass",
    "bucketize",
    "classify",
]
