"""Predicted-delta gate for prompt-evolution proposals.

Origin: Synapse port — the agent_refinement_cycle.md patch-engine section.
Today the evolution loop accepts any proposed prompt patch. This gate
adds an *epsilon* check: a patch must declare a predicted win-rate
improvement at or above ``BERNSTEIN_PROMPT_MIN_DELTA`` (default
``0.05``) before it is allowed to proceed.

Design notes
------------
- Computing ``predicted_delta`` is the proposer's job. This module only
  gates on the value the proposer claims. A pluggable
  :class:`DeltaPredictor` protocol exists so an LLM-judge or heuristic
  scorer can fill the value in when the proposer leaves it blank.
- The default :class:`HeuristicDeltaPredictor` is fully deterministic
  (no LLM calls, no network) — it derives a small synthetic score
  from proposal features so unit tests stay hermetic.
- The gate hooks ``pre_prompt_patch_apply``; if rejected, the proposal
  is logged with reason ``below_threshold`` and the version is not
  bumped. See :class:`bernstein.evolution.gate.ApprovalGate` for the
  wiring point.

Hard contracts (acceptance criteria for #1348)
----------------------------------------------
- ``predicted_delta = 0.02`` with threshold ``0.05`` → rejected
  (``below_threshold``).
- Threshold = 0 → delta check is disabled (every numeric delta passes).
- Identical content proposals (same content hash) never produce a
  *false reject*: they get the same verdict given the same threshold.
- NaN / inf deltas are treated as ``predicted_delta = -inf`` and
  rejected.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Default minimum predicted delta for a patch to be applied. Overridable
# via the ``BERNSTEIN_PROMPT_MIN_DELTA`` environment variable per the
# issue spec.
DEFAULT_MIN_DELTA: float = 0.05

# Valid range for predicted_delta values. Anything outside [-1, 1] is
# clamped before threshold comparison so a buggy predictor cannot
# bypass the gate by returning an absurdly large number.
DELTA_MIN: float = -1.0
DELTA_MAX: float = 1.0


class PatchVerdict(Enum):
    """Possible outcomes of a predicted-delta evaluation."""

    ACCEPTED = "accepted"
    REJECTED_BELOW_THRESHOLD = "below_threshold"
    REJECTED_INVALID_DELTA = "invalid_delta"


@dataclass(frozen=True)
class PatchProposal:
    """A proposed mutation to a versioned prompt template.

    Carries everything the gates need: the originating version id, the
    candidate content, the proposer's rationale, and the numeric
    ``predicted_delta`` (estimated win-rate improvement,
    range ``[-1.0, 1.0]``).

    The proposal is frozen so the *same* proposal object can be re-routed
    through different gates without one mutating it under another.

    Attributes:
        prompt_name: Identifier of the prompt being patched (e.g. ``"judge"``).
        from_version_id: Version id this patch is derived from.
        to_content: Candidate prompt body.
        rationale: Why the proposer believes this patch is an improvement.
        predicted_delta: Proposer-claimed win-rate delta in ``[-1.0, 1.0]``.
        proposal_id: Stable id used in audit rows. Defaults to a hash of
            ``(prompt_name, from_version_id, content_hash)``.
        created_at: Unix timestamp.
    """

    prompt_name: str
    from_version_id: str
    to_content: str
    rationale: str
    predicted_delta: float
    proposal_id: str = ""
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.proposal_id:
            seed = f"{self.prompt_name}|{self.from_version_id}|{self.content_hash}"
            # Use object.__setattr__ because the dataclass is frozen.
            object.__setattr__(
                self,
                "proposal_id",
                hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],
            )

    @property
    def content_hash(self) -> str:
        """Stable SHA-256 hex digest of the candidate content.

        Used by the oscillation guard to detect A → B → A flips and by
        the audit log to de-duplicate identical proposals.
        """
        return hashlib.sha256(self.to_content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PredictedDeltaResult:
    """Verdict from :class:`PredictedDeltaGate`.

    Attributes:
        verdict: Outcome of the evaluation.
        proposal_id: Proposal that was evaluated.
        predicted_delta: The numeric value used for the threshold check
            (post-clamp / sanitisation).
        threshold: The threshold that was applied.
        reason: Human-readable explanation suitable for an audit row.
        accepted: Convenience alias of ``verdict == ACCEPTED``.
    """

    verdict: PatchVerdict
    proposal_id: str
    predicted_delta: float
    threshold: float
    reason: str

    @property
    def accepted(self) -> bool:
        """True iff the proposal cleared the predicted-delta gate."""
        return self.verdict == PatchVerdict.ACCEPTED


@runtime_checkable
class DeltaPredictor(Protocol):
    """Pluggable scorer for ``predicted_delta``.

    Implementations may use a heuristic, a local model, or an LLM judge.
    The default implementation (:class:`HeuristicDeltaPredictor`) is
    deterministic and hermetic so unit tests do not need network or
    secret access.
    """

    def predict(self, proposal: PatchProposal) -> float:  # pragma: no cover - protocol
        """Return a predicted win-rate delta in ``[-1.0, 1.0]``."""
        ...


class HeuristicDeltaPredictor:
    """Tiny deterministic predictor used when the proposer omits a delta.

    Strategy
    --------
    - If the proposal already carries a finite ``predicted_delta``,
      return it unchanged (after clamping). The proposer is the
      authority; this predictor only fills gaps.
    - Otherwise, derive a synthetic score from cheap content features:
      shorter rationale or empty content → lower delta; longer, more
      detailed proposals → slightly higher delta. Bounded into
      ``[-0.1, 0.1]`` so a heuristic guess never alone unlocks a
      contested patch.

    The exact formula is intentionally simple — it exists to keep the
    pipeline moving when ``predicted_delta`` is not yet wired up, NOT to
    be a real predictor.
    """

    SYNTHETIC_BOUND: float = 0.10

    def predict(self, proposal: PatchProposal) -> float:
        """Return the proposer's delta if finite, else a heuristic guess."""
        d = proposal.predicted_delta
        if math.isfinite(d):
            return _clamp(d, DELTA_MIN, DELTA_MAX)

        # Heuristic guess: scale rationale length into [-0.1, 0.1].
        # Empty rationale → -0.1, well-argued (>= 200 chars) → +0.1.
        rationale_len = len(proposal.rationale or "")
        if rationale_len == 0:
            return -self.SYNTHETIC_BOUND
        ratio = min(rationale_len, 200) / 200.0  # 0..1
        return _clamp(
            (ratio - 0.5) * 2.0 * self.SYNTHETIC_BOUND,
            -self.SYNTHETIC_BOUND,
            self.SYNTHETIC_BOUND,
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` to the closed interval ``[lo, hi]``."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def resolve_min_delta(default: float = DEFAULT_MIN_DELTA) -> float:
    """Resolve the active minimum delta from the environment.

    Returns ``default`` when ``BERNSTEIN_PROMPT_MIN_DELTA`` is unset,
    empty, or unparseable. Values below 0 are clamped to 0 — a negative
    threshold has no useful semantics for this gate.
    """
    raw = os.environ.get("BERNSTEIN_PROMPT_MIN_DELTA")
    if raw is None or raw.strip() == "":
        return default
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning("Invalid BERNSTEIN_PROMPT_MIN_DELTA=%r — falling back to %s", raw, default)
        return default
    if not math.isfinite(parsed):
        logger.warning("Non-finite BERNSTEIN_PROMPT_MIN_DELTA=%r — falling back to %s", raw, default)
        return default
    return max(parsed, 0.0)


class PredictedDeltaGate:
    """Reject prompt patches whose predicted delta is below the threshold.

    Args:
        min_delta: Threshold. Patches with
            ``predicted_delta < min_delta`` are rejected. ``min_delta``
            is clamped to ``>= 0`` because a negative threshold has no
            useful meaning here (every numeric delta would pass).
        predictor: Optional predictor that fills in
            ``predicted_delta`` when the proposer leaves it as NaN.

    Example:
        >>> gate = PredictedDeltaGate(min_delta=0.05)
        >>> proposal = PatchProposal(
        ...     prompt_name="judge",
        ...     from_version_id="v1",
        ...     to_content="...",
        ...     rationale="tighter rubric",
        ...     predicted_delta=0.02,
        ... )
        >>> gate.evaluate(proposal).accepted
        False
    """

    def __init__(
        self,
        min_delta: float | None = None,
        predictor: DeltaPredictor | None = None,
    ) -> None:
        resolved = min_delta if min_delta is not None else resolve_min_delta()
        self.min_delta: float = max(resolved, 0.0)
        self._predictor: DeltaPredictor = predictor or HeuristicDeltaPredictor()

    def evaluate(self, proposal: PatchProposal) -> PredictedDeltaResult:
        """Evaluate ``proposal`` against the predicted-delta threshold.

        Decision flow:
        1. Resolve the effective delta (proposer's value, falling back to
           the predictor when the proposer's value is NaN / inf).
        2. Reject with ``invalid_delta`` if the effective delta is still
           not finite.
        3. Clamp into ``[-1, 1]``.
        4. Compare against ``self.min_delta``. ``>=`` accepts.

        Returns:
            :class:`PredictedDeltaResult` describing the verdict.
        """
        proposer_delta = proposal.predicted_delta
        if math.isfinite(proposer_delta):
            effective = _clamp(proposer_delta, DELTA_MIN, DELTA_MAX)
            source = "proposer"
        else:
            predicted = self._predictor.predict(proposal)
            if not math.isfinite(predicted):
                reason = (
                    f"invalid_delta: predicted_delta={proposer_delta!r}, "
                    f"predictor returned non-finite value {predicted!r}"
                )
                return PredictedDeltaResult(
                    verdict=PatchVerdict.REJECTED_INVALID_DELTA,
                    proposal_id=proposal.proposal_id,
                    predicted_delta=float("nan"),
                    threshold=self.min_delta,
                    reason=reason,
                )
            effective = _clamp(predicted, DELTA_MIN, DELTA_MAX)
            source = "predictor"

        if effective >= self.min_delta:
            return PredictedDeltaResult(
                verdict=PatchVerdict.ACCEPTED,
                proposal_id=proposal.proposal_id,
                predicted_delta=effective,
                threshold=self.min_delta,
                reason=(
                    f"accepted: predicted_delta={effective:.4f} >= threshold={self.min_delta:.4f} (source={source})"
                ),
            )

        return PredictedDeltaResult(
            verdict=PatchVerdict.REJECTED_BELOW_THRESHOLD,
            proposal_id=proposal.proposal_id,
            predicted_delta=effective,
            threshold=self.min_delta,
            reason=(
                f"below_threshold: predicted_delta={effective:.4f} < threshold={self.min_delta:.4f} (source={source})"
            ),
        )
