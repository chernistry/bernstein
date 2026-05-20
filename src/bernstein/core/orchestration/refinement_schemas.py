"""Structured critique schemas for the iterative refinement loop.

Defines the JSON-serialisable shapes used by
:mod:`bernstein.core.orchestration.refinement_loop` to pass a per-round
critique between rounds.  The schema is intentionally minimal: a single
``Critique`` payload with bullet-list issues, a 0.0..1.0 score, an
optional veto flag the adversary role can set to short-circuit the
loop, and a free-form rationale.

The critic callback returns a :class:`Critique`; the runner serialises
it via :meth:`Critique.to_dict` so adapters can echo the payload as the
next round's input prefix without losing any field.  Round-trip safety
is enforced by :meth:`Critique.from_dict`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from bernstein.adapters.strict_schema import SchemaViolation

__all__ = [
    "CRITIQUE_ALLOWED_FIELDS",
    "CRITIQUE_ISSUE_ALLOWED_FIELDS",
    "CRITIQUE_SCHEMA_ID",
    "Critique",
    "CritiqueIssue",
    "clamp_score",
]

CRITIQUE_SCHEMA_ID = "bernstein://refinement/critique/v1"
"""Stable schema id for the per-round critique payload."""

CRITIQUE_ISSUE_ALLOWED_FIELDS: frozenset[str] = frozenset({"severity", "message", "suggestion"})
"""Keys a model may emit for a single critique issue."""

CRITIQUE_ALLOWED_FIELDS: frozenset[str] = frozenset({"score", "issues", "veto", "rationale"})
"""Keys a model may emit for a critique payload."""


def _reject_extra_keys(data: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    """Raise :class:`SchemaViolation` if *data* carries undeclared keys.

    Mirrors ``additionalProperties: false`` for the dataclass-backed
    critique payload so a hallucinated key is rejected at parse time
    rather than silently dropped.
    """
    extras = sorted(set(data) - allowed)
    if extras:
        joined = ", ".join(extras)
        raise SchemaViolation(
            f"{where} carried undeclared fields (additionalProperties): {joined}",
            fields=tuple(extras),
        )


def clamp_score(value: float) -> float:
    """Clamp *value* into the closed interval ``[0.0, 1.0]``.

    Args:
        value: Any real number.

    Returns:
        ``value`` clamped into ``[0.0, 1.0]``.
    """
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _empty_issues() -> list[CritiqueIssue]:
    """Return a typed empty list of :class:`CritiqueIssue`."""
    return []


@dataclass(frozen=True)
class CritiqueIssue:
    """One bullet-point critique finding.

    Attributes:
        severity: ``"low"``, ``"medium"``, or ``"high"``.  Free-form
            string; the runner treats unknown values as ``"low"`` for
            sorting only.
        message: Human-readable critique.  Truncated at write time
            by the calling critic; the schema does not enforce a cap.
        suggestion: Optional concrete remediation hint.
    """

    severity: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CritiqueIssue:
        """Build a :class:`CritiqueIssue` from a parsed dict."""
        return cls(
            severity=str(data.get("severity", "low")),
            message=str(data.get("message", "")),
            suggestion=str(data.get("suggestion", "")),
        )

    @classmethod
    def from_dict_strict(cls, data: dict[str, Any]) -> CritiqueIssue:
        """Build a :class:`CritiqueIssue`, rejecting any undeclared key.

        Raises:
            SchemaViolation: If *data* carries a key outside
                :data:`CRITIQUE_ISSUE_ALLOWED_FIELDS`.
        """
        _reject_extra_keys(data, CRITIQUE_ISSUE_ALLOWED_FIELDS, "critique issue")
        return cls.from_dict(data)


@dataclass(frozen=True)
class Critique:
    """Per-round critique payload consumed by the refinement runner.

    Attributes:
        score: 0.0..1.0 quality estimate.  Higher is better.  The
            runner uses this to detect plateaus and threshold gates.
        issues: Bullet-list of findings.  May be empty when the critic
            is satisfied.
        veto: When ``True`` the adversary asserts that the artefact
            should be rejected outright; the runner stops the loop with
            ``early_stop_reason="adversary_veto"``.
        rationale: Free-form summary echoed into the next-round prompt
            prefix.  Long rationales are the critic's responsibility to
            cap; the runner stores whatever is returned.
    """

    score: float
    issues: list[CritiqueIssue] = field(default_factory=_empty_issues)
    veto: bool = False
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "score": clamp_score(self.score),
            "issues": [i.to_dict() for i in self.issues],
            "veto": self.veto,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Critique:
        """Build a :class:`Critique` from a parsed dict.

        Missing fields fall back to neutral defaults so adapter-side
        critics need not emit every key for an "all clear" round.
        """
        raw_issues_any: Any = data.get("issues", []) or []
        issues: list[CritiqueIssue] = []
        if isinstance(raw_issues_any, list):
            raw_issues = cast(list[Any], raw_issues_any)
            for entry in raw_issues:
                if isinstance(entry, dict):
                    issues.append(CritiqueIssue.from_dict(cast(dict[str, Any], entry)))
        return cls(
            score=clamp_score(float(data.get("score", 0.0))),
            issues=issues,
            veto=bool(data.get("veto", False)),
            rationale=str(data.get("rationale", "")),
        )

    @classmethod
    def from_dict_strict(cls, data: dict[str, Any]) -> Critique:
        """Build a :class:`Critique`, rejecting any undeclared key.

        Use on the AI-output parse path: a hallucinated top-level key (or a
        hallucinated key inside any issue) raises rather than being
        silently dropped, so the runner can bound its retries.

        Raises:
            SchemaViolation: If the payload or any issue carries a key
                outside the declared field set.
        """
        _reject_extra_keys(data, CRITIQUE_ALLOWED_FIELDS, "critique")
        raw_issues_any: Any = data.get("issues", []) or []
        issues: list[CritiqueIssue] = []
        if isinstance(raw_issues_any, list):
            raw_issues = cast(list[Any], raw_issues_any)
            for entry in raw_issues:
                if isinstance(entry, dict):
                    issues.append(CritiqueIssue.from_dict_strict(cast(dict[str, Any], entry)))
        return cls(
            score=clamp_score(float(data.get("score", 0.0))),
            issues=issues,
            veto=bool(data.get("veto", False)),
            rationale=str(data.get("rationale", "")),
        )
