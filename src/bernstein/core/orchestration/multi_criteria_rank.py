"""Multi-criteria ranking for best-of-N candidates via TOPSIS.

The orchestrator's :mod:`bernstein.core.orchestration.best_of_n` runner
collapses every candidate down to a single blended scalar.  That hides
trade-offs an operator cares about: a candidate that is marginally
cheaper but materially less safe should not silently win on a risky
change.

This module implements *Technique for Order of Preference by Similarity
to Ideal Solution* (TOPSIS), the simplest of the classical Multi-Criteria
Decision Making methods.  Each candidate is described by a score vector
on N axes (typically ``correctness``, ``cost``, ``latency``,
``reversibility``).  We:

1. Normalise each axis across the candidate set (vector norm).
2. Apply caller-supplied per-axis weights — identity by default.
3. Compute the per-axis ideal (best) and anti-ideal (worst) points,
   respecting whether each axis is a benefit (higher = better) or a cost
   (lower = better).
4. Score each candidate by its *closeness coefficient* — Euclidean
   distance to the anti-ideal divided by the sum of distances to ideal
   and anti-ideal.
5. Rank by closeness, breaking ties on the original key for determinism.

Why TOPSIS only:  the issue spec is explicit — the rest of the Synapse
MCDM7 chain (AHP / SMART / DEMATEL / BWM) is overkill for best-of-N where
N <= 5.  Constraining the surface keeps the runner deterministic and
auditable.

Pure-Python implementation: no numpy dependency, ~150 lines of stdlib
``math``.  Inputs are validated up front so a malformed criteria spec
fails loudly instead of producing nonsense ranks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "Candidate",
    "Criterion",
    "CriterionProfile",
    "RankedCandidate",
    "TopsisError",
    "build_criterion_profile",
    "parse_criteria_csv",
    "rank_candidates",
    "render_ranking_json",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TopsisError(ValueError):
    """Raised when the inputs to :func:`rank_candidates` are malformed.

    Subclasses :class:`ValueError` so callers that already handle
    ``ValueError`` (the existing ``select_best`` does so for empty
    candidate sets) keep working unchanged.
    """


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


_BENEFIT: Final[str] = "benefit"
_COST: Final[str] = "cost"
_VALID_DIRECTIONS: Final[frozenset[str]] = frozenset({_BENEFIT, _COST})


@dataclass(frozen=True)
class Criterion:
    """One ranking axis.

    Attributes:
        name: Axis identifier — must match the keys used in every
            :class:`Candidate.scores` mapping.
        direction: ``"benefit"`` when higher is better (correctness,
            reversibility) or ``"cost"`` when lower is better (cost,
            latency).
        weight: Importance multiplier.  Must be non-negative.  Weights
            do not need to sum to 1 — :func:`rank_candidates`
            normalises internally.
    """

    name: str
    direction: str = _BENEFIT
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.name:
            raise TopsisError("Criterion.name must be a non-empty string")
        if self.direction not in _VALID_DIRECTIONS:
            raise TopsisError(f"Criterion.direction must be 'benefit' or 'cost', got {self.direction!r}")
        if math.isnan(self.weight) or math.isinf(self.weight):
            raise TopsisError(f"Criterion.weight must be finite, got {self.weight!r}")
        if self.weight < 0.0:
            raise TopsisError(f"Criterion.weight must be non-negative, got {self.weight!r}")


@dataclass(frozen=True)
class Candidate:
    """One ranking input — an opaque key and a per-axis score vector.

    The ``key`` is only used for tie-breaking and audit; it can be the
    candidate task id, an index, or any stable string.  ``scores`` must
    contain every axis named by the :class:`CriterionProfile` passed to
    :func:`rank_candidates`; any extra axes are ignored.
    """

    key: str
    scores: Mapping[str, float]


@dataclass(frozen=True)
class CriterionProfile:
    """A complete TOPSIS configuration — the criteria and their weights.

    Use :func:`build_criterion_profile` to construct a profile from a
    plain list of axis names; that helper applies the identity-weights
    default required by the issue spec.
    """

    criteria: tuple[Criterion, ...]

    def __post_init__(self) -> None:
        if not self.criteria:
            raise TopsisError("CriterionProfile must contain at least one criterion")
        seen: set[str] = set()
        for c in self.criteria:
            if c.name in seen:
                raise TopsisError(f"Duplicate criterion name: {c.name!r}")
            seen.add(c.name)

    @property
    def names(self) -> tuple[str, ...]:
        """Ordered tuple of criterion names — matches axis order."""
        return tuple(c.name for c in self.criteria)


@dataclass(frozen=True)
class RankedCandidate:
    """One row of :func:`rank_candidates` output.

    Attributes:
        key: Echoed input key, lets callers join back to their domain
            objects.
        rank: 1-based rank; ``1`` is the winner.
        closeness: TOPSIS closeness coefficient in ``[0.0, 1.0]``.  1.0
            means coincident with the ideal point, 0.0 with the anti-
            ideal.  Ties break by stable ascending key.
        normalised_scores: Per-axis weight-applied normalised score for
            audit / explanation surfaces.
    """

    key: str
    rank: int
    closeness: float
    normalised_scores: Mapping[str, float] = field(default_factory=lambda: dict[str, float]())


# ---------------------------------------------------------------------------
# Profile helpers
# ---------------------------------------------------------------------------


def build_criterion_profile(
    criteria: Sequence[str],
    weights: Sequence[float] | None = None,
    *,
    cost_axes: Sequence[str] | None = None,
) -> CriterionProfile:
    """Construct a :class:`CriterionProfile` from a plain axis list.

    Identity weights are applied when *weights* is None — this is the
    default required by the issue spec.  ``cost_axes`` lists axes for
    which lower is better (defaults to the well-known ``cost`` /
    ``latency``); everything else is treated as a benefit axis.
    """
    if not criteria:
        raise TopsisError("criteria must be a non-empty sequence")
    if weights is None:
        weights = [1.0] * len(criteria)
    if len(weights) != len(criteria):
        raise TopsisError(f"weights length {len(weights)} does not match criteria length {len(criteria)}")
    cost_set = set(cost_axes) if cost_axes is not None else {"cost", "latency"}
    built: list[Criterion] = []
    for name, w in zip(criteria, weights, strict=True):
        direction = _COST if name in cost_set else _BENEFIT
        built.append(Criterion(name=name, direction=direction, weight=float(w)))
    return CriterionProfile(criteria=tuple(built))


def parse_criteria_csv(value: str) -> tuple[str, ...]:
    """Parse a comma-separated criteria spec from the CLI surface.

    Whitespace around each token is stripped.  Empty tokens are
    rejected so a stray comma surfaces as an error instead of silently
    producing a phantom axis.
    """
    if not value or not value.strip():
        raise TopsisError("criteria CSV must be non-empty")
    parts = [token.strip() for token in value.split(",")]
    if any(not p for p in parts):
        raise TopsisError(f"criteria CSV {value!r} contains empty tokens")
    return tuple(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def rank_candidates(
    candidates: Sequence[Candidate],
    criteria: Sequence[str] | CriterionProfile,
    weights: Sequence[float] | None = None,
) -> list[RankedCandidate]:
    """Rank *candidates* by closeness to the ideal point under TOPSIS.

    Args:
        candidates: Inputs to rank.  An empty list returns an empty list.
            A single-candidate list trivially returns rank 1 with
            closeness 1.0 (no comparison possible).
        criteria: Either a sequence of axis names — in which case
            identity weights are applied — or a fully built
            :class:`CriterionProfile`.
        weights: Optional per-axis weights.  Only honoured when
            *criteria* is a sequence of names; passing both *weights*
            and a :class:`CriterionProfile` raises :class:`TopsisError`.

    Returns:
        New list of :class:`RankedCandidate` records sorted by
        descending closeness.  Tie-break is stable ascending key for
        determinism (so the same input always picks the same winner).

    Raises:
        TopsisError: When any input is malformed (missing score, NaN,
            duplicate keys, weights inconsistent with criteria).
    """
    if not candidates:
        return []

    profile = _resolve_profile(criteria, weights)
    n = len(candidates)

    if n == 1:
        # Degenerate: no ideal/anti-ideal pair to compare against.  Emit
        # rank 1, closeness 1.0 so the caller can still surface the
        # single candidate uniformly.
        only = candidates[0]
        _check_scores_present(only, profile)
        return [
            RankedCandidate(
                key=only.key,
                rank=1,
                closeness=1.0,
                normalised_scores={name: 0.0 for name in profile.names},
            )
        ]

    _check_duplicate_keys(candidates)

    matrix = _build_matrix(candidates, profile)
    normalised = _vector_normalise(matrix)
    weighted = _apply_weights(normalised, profile)
    ideal, anti_ideal = _ideal_points(weighted, profile)
    closeness = _closeness(weighted, ideal, anti_ideal)

    indexed = list(zip(range(n), candidates, closeness, strict=True))
    # Stable sort: descending closeness, ascending key for determinism.
    indexed.sort(key=lambda triple: (-triple[2], triple[1].key))

    out: list[RankedCandidate] = []
    for rank_idx, (orig_idx, cand, c) in enumerate(indexed, start=1):
        per_axis = {name: weighted[orig_idx][i] for i, name in enumerate(profile.names)}
        out.append(
            RankedCandidate(
                key=cand.key,
                rank=rank_idx,
                closeness=c,
                normalised_scores=per_axis,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_profile(
    criteria: Sequence[str] | CriterionProfile,
    weights: Sequence[float] | None,
) -> CriterionProfile:
    if isinstance(criteria, CriterionProfile):
        if weights is not None:
            raise TopsisError("weights cannot be combined with a pre-built CriterionProfile")
        return criteria
    return build_criterion_profile(list(criteria), weights)


def _check_duplicate_keys(candidates: Sequence[Candidate]) -> None:
    seen: set[str] = set()
    for cand in candidates:
        if cand.key in seen:
            raise TopsisError(f"Duplicate candidate key: {cand.key!r}")
        seen.add(cand.key)


def _check_scores_present(cand: Candidate, profile: CriterionProfile) -> None:
    for name in profile.names:
        if name not in cand.scores:
            raise TopsisError(f"Candidate {cand.key!r} is missing score for criterion {name!r}")
        value: object = cand.scores[name]
        # Reject booleans (which are ``int`` subclasses) and any non-real
        # input — callers sometimes pass through ``Any`` mappings even
        # though the declared mapping type is ``Mapping[str, float]``.
        if isinstance(value, bool) or not isinstance(value, (int, float)):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TopsisError(f"Candidate {cand.key!r} score for {name!r} must be a real number")
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            raise TopsisError(f"Candidate {cand.key!r} has non-finite score for {name!r}: {value!r}")


def _build_matrix(candidates: Sequence[Candidate], profile: CriterionProfile) -> list[list[float]]:
    matrix: list[list[float]] = []
    for cand in candidates:
        _check_scores_present(cand, profile)
        row = [float(cand.scores[name]) for name in profile.names]
        matrix.append(row)
    return matrix


def _vector_normalise(matrix: list[list[float]]) -> list[list[float]]:
    """Vector (L2) normalisation per column — classical TOPSIS step.

    A column that is identically zero stays zero — every candidate is
    equally far from the ideal on that axis, so it contributes nothing
    to the ranking.
    """
    if not matrix:
        return []
    n_cols = len(matrix[0])
    norms: list[float] = []
    for j in range(n_cols):
        col_sq = sum(row[j] * row[j] for row in matrix)
        norms.append(math.sqrt(col_sq))
    out: list[list[float]] = []
    for row in matrix:
        new_row: list[float] = []
        for j, v in enumerate(row):
            new_row.append(v / norms[j] if norms[j] > 0.0 else 0.0)
        out.append(new_row)
    return out


def _apply_weights(matrix: list[list[float]], profile: CriterionProfile) -> list[list[float]]:
    total = sum(c.weight for c in profile.criteria)
    if total <= 0.0:
        raise TopsisError("Sum of criterion weights must be positive")
    normalised_weights = [c.weight / total for c in profile.criteria]
    return [[v * w for v, w in zip(row, normalised_weights, strict=True)] for row in matrix]


def _ideal_points(matrix: list[list[float]], profile: CriterionProfile) -> tuple[list[float], list[float]]:
    if not matrix:
        return ([], [])
    n_cols = len(matrix[0])
    ideal: list[float] = []
    anti: list[float] = []
    for j in range(n_cols):
        column = [row[j] for row in matrix]
        if profile.criteria[j].direction == _BENEFIT:
            ideal.append(max(column))
            anti.append(min(column))
        else:
            ideal.append(min(column))
            anti.append(max(column))
    return (ideal, anti)


def render_ranking_json(
    ranked: Sequence[RankedCandidate],
    profile: CriterionProfile,
    *,
    precision: int = 6,
) -> dict[str, object]:
    """Render *ranked* as a JSON-serialisable mapping for ``.sdd`` artefacts.

    The shape is stable so snapshot tests can pin the operator-facing
    surface; numeric values are rounded to *precision* decimals to keep
    the snapshot deterministic across platforms (where the last 1-2
    ULPs of a ``math.sqrt`` may differ).
    """
    rows: list[dict[str, object]] = []
    for r in ranked:
        rows.append(
            {
                "key": r.key,
                "rank": r.rank,
                "closeness": round(r.closeness, precision),
                "normalised_scores": {
                    name: round(r.normalised_scores.get(name, 0.0), precision) for name in profile.names
                },
            }
        )
    winner_key = rows[0]["key"] if rows else None
    return {
        "method": "topsis",
        "criteria": [{"name": c.name, "direction": c.direction, "weight": c.weight} for c in profile.criteria],
        "winner": winner_key,
        "ranking": rows,
    }


def _closeness(matrix: list[list[float]], ideal: list[float], anti: list[float]) -> list[float]:
    out: list[float] = []
    for row in matrix:
        d_plus = math.sqrt(sum((row[j] - ideal[j]) ** 2 for j in range(len(row))))
        d_minus = math.sqrt(sum((row[j] - anti[j]) ** 2 for j in range(len(row))))
        denom = d_plus + d_minus
        if denom <= 0.0:
            # All candidates collapsed to the same point — assign equal
            # closeness so the stable-sort tie-break decides the order.
            out.append(0.0)
        else:
            out.append(d_minus / denom)
    return out
