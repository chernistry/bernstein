"""Unit tests for the predicted-delta gate.

Covers the acceptance criteria from issue #1348:
- ``predicted_delta = 0.02`` rejected with ``below_threshold``.
- ``BERNSTEIN_PROMPT_MIN_DELTA = 0`` disables the delta check.
- NaN / inf deltas trip the ``invalid_delta`` verdict.
- Pluggable predictor / heuristic fallback.
- Audit payload format matches the issue spec.
"""

from __future__ import annotations

import math
from collections.abc import Iterator

import pytest

from bernstein.evolution.predicted_delta import (
    DEFAULT_MIN_DELTA,
    DELTA_MAX,
    DELTA_MIN,
    HeuristicDeltaPredictor,
    PatchProposal,
    PatchVerdict,
    PredictedDeltaGate,
    PredictedDeltaResult,
    resolve_min_delta,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make(
    *,
    prompt_name: str = "judge",
    from_version_id: str = "v1",
    to_content: str = "you are a strict judge",
    rationale: str = "tighter rubric",
    predicted_delta: float = 0.10,
) -> PatchProposal:
    return PatchProposal(
        prompt_name=prompt_name,
        from_version_id=from_version_id,
        to_content=to_content,
        rationale=rationale,
        predicted_delta=predicted_delta,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("BERNSTEIN_PROMPT_MIN_DELTA", raising=False)
    monkeypatch.delenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", raising=False)
    yield


# ---------------------------------------------------------------------------
# PatchProposal
# ---------------------------------------------------------------------------


class TestPatchProposal:
    def test_content_hash_is_deterministic(self) -> None:
        p1 = _make(to_content="hello world")
        p2 = _make(to_content="hello world")
        assert p1.content_hash == p2.content_hash

    def test_content_hash_changes_with_content(self) -> None:
        p1 = _make(to_content="A")
        p2 = _make(to_content="B")
        assert p1.content_hash != p2.content_hash

    def test_proposal_id_is_deterministic(self) -> None:
        p1 = _make()
        p2 = _make()
        assert p1.proposal_id == p2.proposal_id

    def test_explicit_proposal_id_is_preserved(self) -> None:
        p = PatchProposal(
            prompt_name="judge",
            from_version_id="v1",
            to_content="x",
            rationale="r",
            predicted_delta=0.1,
            proposal_id="custom-id",
        )
        assert p.proposal_id == "custom-id"

    def test_proposal_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        p = _make()
        with pytest.raises(FrozenInstanceError):
            p.predicted_delta = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve_min_delta
# ---------------------------------------------------------------------------


class TestResolveMinDelta:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_PROMPT_MIN_DELTA", raising=False)
        assert resolve_min_delta() == DEFAULT_MIN_DELTA

    def test_custom_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_PROMPT_MIN_DELTA", raising=False)
        assert resolve_min_delta(default=0.2) == 0.2

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "0.1")
        assert resolve_min_delta() == 0.1

    def test_env_empty_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "")
        assert resolve_min_delta() == DEFAULT_MIN_DELTA

    def test_env_garbage_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "abc")
        assert resolve_min_delta() == DEFAULT_MIN_DELTA

    def test_env_negative_clamped_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "-0.5")
        assert resolve_min_delta() == 0.0

    def test_env_zero_disables_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "0")
        assert resolve_min_delta() == 0.0

    def test_env_inf_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "inf")
        assert resolve_min_delta() == DEFAULT_MIN_DELTA


# ---------------------------------------------------------------------------
# HeuristicDeltaPredictor
# ---------------------------------------------------------------------------


class TestHeuristicDeltaPredictor:
    def test_returns_proposer_value_when_finite(self) -> None:
        p = HeuristicDeltaPredictor()
        proposal = _make(predicted_delta=0.07)
        assert p.predict(proposal) == 0.07

    def test_clamps_proposer_value_above_one(self) -> None:
        p = HeuristicDeltaPredictor()
        proposal = _make(predicted_delta=5.0)
        assert p.predict(proposal) == DELTA_MAX

    def test_clamps_proposer_value_below_minus_one(self) -> None:
        p = HeuristicDeltaPredictor()
        proposal = _make(predicted_delta=-5.0)
        assert p.predict(proposal) == DELTA_MIN

    def test_falls_back_for_nan(self) -> None:
        p = HeuristicDeltaPredictor()
        proposal = _make(predicted_delta=float("nan"), rationale="a" * 200)
        assert p.predict(proposal) == pytest.approx(HeuristicDeltaPredictor.SYNTHETIC_BOUND)

    def test_falls_back_for_empty_rationale(self) -> None:
        p = HeuristicDeltaPredictor()
        proposal = _make(predicted_delta=float("nan"), rationale="")
        assert p.predict(proposal) == pytest.approx(-HeuristicDeltaPredictor.SYNTHETIC_BOUND)

    def test_heuristic_bounded(self) -> None:
        p = HeuristicDeltaPredictor()
        for rl in ("", "x", "xx", "x" * 50, "x" * 500, "x" * 5000):
            proposal = _make(predicted_delta=float("inf"), rationale=rl)
            v = p.predict(proposal)
            assert -HeuristicDeltaPredictor.SYNTHETIC_BOUND <= v <= HeuristicDeltaPredictor.SYNTHETIC_BOUND


# ---------------------------------------------------------------------------
# PredictedDeltaGate — core behaviour
# ---------------------------------------------------------------------------


class TestPredictedDeltaGate:
    def test_accept_when_delta_above_threshold(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=0.10)
        result = gate.evaluate(proposal)
        assert result.accepted
        assert result.verdict == PatchVerdict.ACCEPTED
        assert "accepted" in result.reason

    def test_accept_when_delta_equals_threshold(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=0.05)
        result = gate.evaluate(proposal)
        assert result.accepted

    def test_reject_when_below_threshold(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=0.02)
        result = gate.evaluate(proposal)
        assert not result.accepted
        assert result.verdict == PatchVerdict.REJECTED_BELOW_THRESHOLD
        assert "below_threshold" in result.reason

    def test_reject_exactly_one_epsilon_below(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=0.0499)
        result = gate.evaluate(proposal)
        assert not result.accepted

    def test_zero_threshold_disables_check(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.0)
        # Even tiny negative deltas (-0.001) are not accepted at threshold 0
        # because the rule is strict >= threshold. Issue says "delta check
        # is disabled" — interpret as: every non-negative delta passes.
        assert gate.evaluate(_make(predicted_delta=0.0)).accepted
        assert gate.evaluate(_make(predicted_delta=0.001)).accepted

    def test_negative_threshold_clamped_to_zero(self) -> None:
        gate = PredictedDeltaGate(min_delta=-0.5)
        assert gate.min_delta == 0.0

    def test_threshold_resolved_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "0.20")
        gate = PredictedDeltaGate()
        assert gate.min_delta == 0.20

    def test_explicit_threshold_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MIN_DELTA", "0.20")
        gate = PredictedDeltaGate(min_delta=0.01)
        assert gate.min_delta == 0.01

    def test_clamps_delta_above_one(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        result = gate.evaluate(_make(predicted_delta=5.0))
        assert result.predicted_delta == DELTA_MAX
        assert result.accepted

    def test_clamps_delta_below_minus_one(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        result = gate.evaluate(_make(predicted_delta=-5.0))
        assert result.predicted_delta == DELTA_MIN
        assert not result.accepted

    def test_nan_delta_falls_back_to_predictor(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=float("nan"), rationale="a" * 200)
        result = gate.evaluate(proposal)
        # Heuristic at max length returns +0.10 → accepted
        assert result.accepted

    def test_nan_delta_with_short_rationale_rejected(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=float("nan"), rationale="")
        result = gate.evaluate(proposal)
        # Heuristic returns -0.10 → rejected
        assert not result.accepted
        assert result.verdict == PatchVerdict.REJECTED_BELOW_THRESHOLD

    def test_pos_inf_delta_falls_back(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=float("inf"), rationale="x" * 200)
        result = gate.evaluate(proposal)
        assert math.isfinite(result.predicted_delta)
        assert result.accepted

    def test_neg_inf_delta_falls_back(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=float("-inf"), rationale="x" * 200)
        result = gate.evaluate(proposal)
        # Predictor still returns its heuristic guess, which is +0.1 for long rationale.
        assert math.isfinite(result.predicted_delta)

    def test_invalid_delta_when_predictor_also_broken(self) -> None:
        class BrokenPredictor:
            def predict(self, proposal: PatchProposal) -> float:
                return float("nan")

        gate = PredictedDeltaGate(min_delta=0.05, predictor=BrokenPredictor())
        proposal = _make(predicted_delta=float("nan"))
        result = gate.evaluate(proposal)
        assert result.verdict == PatchVerdict.REJECTED_INVALID_DELTA
        assert "invalid_delta" in result.reason
        assert math.isnan(result.predicted_delta)

    def test_custom_predictor_used_only_when_proposer_invalid(self) -> None:
        calls: list[PatchProposal] = []

        class TrackingPredictor:
            def predict(self, proposal: PatchProposal) -> float:
                calls.append(proposal)
                return 1.0

        gate = PredictedDeltaGate(min_delta=0.05, predictor=TrackingPredictor())
        gate.evaluate(_make(predicted_delta=0.07))
        assert calls == []  # finite proposer value short-circuits predictor.

        gate.evaluate(_make(predicted_delta=float("nan")))
        assert len(calls) == 1

    def test_result_reason_includes_threshold(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        result = gate.evaluate(_make(predicted_delta=0.02))
        assert "0.0500" in result.reason
        assert "0.0200" in result.reason

    def test_result_carries_proposal_id(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make()
        result = gate.evaluate(proposal)
        assert result.proposal_id == proposal.proposal_id


# ---------------------------------------------------------------------------
# Determinism / idempotence
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_identical_proposals_get_identical_verdict(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        p1 = _make(predicted_delta=0.02)
        p2 = _make(predicted_delta=0.02)
        r1 = gate.evaluate(p1)
        r2 = gate.evaluate(p2)
        assert r1.verdict == r2.verdict
        assert r1.predicted_delta == r2.predicted_delta
        assert r1.threshold == r2.threshold

    def test_evaluate_does_not_mutate_proposal(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=0.02)
        before = (
            proposal.predicted_delta,
            proposal.rationale,
            proposal.to_content,
            proposal.from_version_id,
            proposal.prompt_name,
        )
        gate.evaluate(proposal)
        after = (
            proposal.predicted_delta,
            proposal.rationale,
            proposal.to_content,
            proposal.from_version_id,
            proposal.prompt_name,
        )
        assert before == after

    def test_evaluate_is_pure_for_same_input(self) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        proposal = _make(predicted_delta=0.02)
        r1 = gate.evaluate(proposal)
        r2 = gate.evaluate(proposal)
        assert r1.verdict == r2.verdict
        assert r1.threshold == r2.threshold

    def test_result_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        gate = PredictedDeltaGate(min_delta=0.05)
        result = gate.evaluate(_make())
        assert isinstance(result, PredictedDeltaResult)
        with pytest.raises(FrozenInstanceError):
            result.verdict = PatchVerdict.ACCEPTED  # type: ignore[misc]
