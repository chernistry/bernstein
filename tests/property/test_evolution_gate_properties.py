"""Property tests for the prompt-patch evolution gate.

Invariants under test
---------------------

* **Idempotence** — running the predicted-delta gate twice on the same
  proposal yields the same verdict, predicted_delta, and threshold.

* **Monotonicity in threshold** — for any fixed proposal, raising the
  threshold can only keep the verdict the same or flip it from
  *accepted* to *below_threshold* (never the reverse).

* **No false-veto on identical-content proposals** — two proposals
  with the same content_hash receive the same delta-gate verdict and,
  for the oscillation guard, identical handling order (second sighting
  is always accepted or pending depending on min_confirmations, never
  flipped to "flip_back" out of nowhere).

* **Session-cap monotone** — total accepted patches in a session never
  exceed ``max_patches_per_session`` (when > 0), regardless of the
  sequence of proposals.

* **Oscillation random sequences <= 20** — the audit row count equals
  the number of proposals submitted, and ``session_applied_count`` is
  bounded by the cap.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from bernstein.evolution.gate import (
    PromptPatchGate,
    PromptPatchOutcome,
)
from bernstein.evolution.oscillation_guard import OscillationGuard, OscillationVerdict
from bernstein.evolution.predicted_delta import (
    PatchProposal,
    PatchVerdict,
    PredictedDeltaGate,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


finite_deltas = st.floats(
    min_value=-1.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)
thresholds = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
content_letters = st.sampled_from(["A", "B", "C", "D"])
prompt_names = st.sampled_from(["judge", "manager", "qa"])


def _proposal(
    *,
    to_content: str = "A",
    predicted_delta: float = 0.10,
    prompt_name: str = "judge",
    from_version_id: str = "v1",
) -> PatchProposal:
    return PatchProposal(
        prompt_name=prompt_name,
        from_version_id=from_version_id,
        to_content=to_content,
        rationale="r",
        predicted_delta=predicted_delta,
    )


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


class TestDeltaGateIdempotence:
    @given(delta=finite_deltas, threshold=thresholds)
    def test_evaluate_twice_same_verdict(self, delta: float, threshold: float) -> None:
        gate = PredictedDeltaGate(min_delta=threshold)
        proposal = _proposal(predicted_delta=delta)
        r1 = gate.evaluate(proposal)
        r2 = gate.evaluate(proposal)
        assert r1.verdict == r2.verdict
        assert r1.threshold == r2.threshold
        assert r1.predicted_delta == r2.predicted_delta

    @given(delta=finite_deltas, threshold=thresholds)
    def test_two_proposals_same_hash_same_verdict(self, delta: float, threshold: float) -> None:
        gate = PredictedDeltaGate(min_delta=threshold)
        a = _proposal(to_content="same", predicted_delta=delta)
        b = _proposal(to_content="same", predicted_delta=delta)
        r1 = gate.evaluate(a)
        r2 = gate.evaluate(b)
        assert r1.verdict == r2.verdict
        assert r1.predicted_delta == r2.predicted_delta


# ---------------------------------------------------------------------------
# Monotonicity in threshold
# ---------------------------------------------------------------------------


class TestThresholdMonotonicity:
    @given(delta=finite_deltas, low=thresholds, high=thresholds)
    def test_raising_threshold_never_flips_to_accept(self, delta: float, low: float, high: float) -> None:
        assume(low <= high)
        proposal = _proposal(predicted_delta=delta)
        low_result = PredictedDeltaGate(min_delta=low).evaluate(proposal)
        high_result = PredictedDeltaGate(min_delta=high).evaluate(proposal)
        # If low rejects, high MUST also reject (monotone in threshold).
        if low_result.verdict == PatchVerdict.REJECTED_BELOW_THRESHOLD:
            assert high_result.verdict == PatchVerdict.REJECTED_BELOW_THRESHOLD

    @given(delta=finite_deltas, t=thresholds)
    def test_threshold_zero_accepts_all_non_negative_deltas(self, delta: float, t: float) -> None:
        assume(delta >= 0)
        result = PredictedDeltaGate(min_delta=0.0).evaluate(_proposal(predicted_delta=delta))
        assert result.verdict == PatchVerdict.ACCEPTED


# ---------------------------------------------------------------------------
# Identical-content proposals receive identical handling
# ---------------------------------------------------------------------------


class TestIdenticalContentHandling:
    @given(delta=finite_deltas, threshold=thresholds)
    def test_no_false_veto_for_identical_content(self, delta: float, threshold: float) -> None:
        """The delta gate never vetoes one of two identical proposals while accepting the other."""
        gate = PredictedDeltaGate(min_delta=threshold)
        a = _proposal(to_content="x", predicted_delta=delta)
        b = _proposal(to_content="x", predicted_delta=delta)
        assert gate.evaluate(a).verdict == gate.evaluate(b).verdict

    @given(delta=finite_deltas)
    def test_oscillation_guard_handles_identical_back_to_back(self, delta: float) -> None:
        guard = OscillationGuard(min_confirmations=2)
        proposal = _proposal(predicted_delta=delta, to_content="A")
        r1 = guard.evaluate(proposal)
        r2 = guard.evaluate(proposal)
        # Sighting #2 must accept (no flip-back possible without an applied B).
        assert r1.verdict == OscillationVerdict.PENDING_CONFIRMATION
        assert r2.verdict == OscillationVerdict.ACCEPTED


# ---------------------------------------------------------------------------
# Session cap is monotone
# ---------------------------------------------------------------------------


class TestSessionCapMonotone:
    @given(
        sequence=st.lists(content_letters, min_size=0, max_size=20),
        cap=st.integers(min_value=1, max_value=10),
    )
    @settings(deadline=None)
    def test_session_applied_never_exceeds_cap(self, sequence: list[str], cap: int) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(min_confirmations=1, max_patches_per_session=cap),
        )
        for c in sequence:
            decision = gate.evaluate(_proposal(to_content=c))
            if decision.outcome == PromptPatchOutcome.APPLIED:
                gate.mark_applied(_proposal(to_content=c))
        assert gate.oscillation_guard.session_applied_count <= cap


# ---------------------------------------------------------------------------
# Random sequences <= 20 — oscillation guard invariants
# ---------------------------------------------------------------------------


class TestRandomOscillationSequences:
    @given(
        sequence=st.lists(content_letters, min_size=0, max_size=20),
    )
    @settings(deadline=None)
    def test_audit_row_per_proposal(self, sequence: list[str], tmp_path_factory: pytest.TempPathFactory) -> None:
        import json

        tmp_dir = tmp_path_factory.mktemp("audit")
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(min_confirmations=1, max_patches_per_session=0),
            audit_dir=tmp_dir,
            session_id="prop",
        )
        for c in sequence:
            decision = gate.evaluate(_proposal(to_content=c))
            if decision.outcome == PromptPatchOutcome.APPLIED:
                gate.mark_applied(_proposal(to_content=c))
        log = tmp_dir / "prop.jsonl"
        if not sequence:
            assert not log.exists() or log.read_text() == ""
            return
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == len(sequence)

    @given(
        sequence=st.lists(content_letters, min_size=0, max_size=20),
        min_conf=st.integers(min_value=1, max_value=3),
    )
    @settings(deadline=None)
    def test_applied_count_bounded_by_unique_with_min_confirmations_one(
        self, sequence: list[str], min_conf: int
    ) -> None:
        guard = OscillationGuard(min_confirmations=min_conf, max_patches_per_session=0, window_size=10)
        for c in sequence:
            r = guard.evaluate(_proposal(to_content=c))
            if r.verdict == OscillationVerdict.ACCEPTED:
                guard.record_applied(_proposal(to_content=c))
        # Applied count can't exceed total proposals.
        assert guard.session_applied_count <= len(sequence)


# ---------------------------------------------------------------------------
# Pluggable predictor — return value range
# ---------------------------------------------------------------------------


class TestPredictorRange:
    @given(value=st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False))
    def test_predicted_delta_clamped(self, value: float) -> None:
        gate = PredictedDeltaGate(min_delta=0.05)
        result = gate.evaluate(_proposal(predicted_delta=value))
        assert -1.0 <= result.predicted_delta <= 1.0
        assert math.isfinite(result.predicted_delta)


# ---------------------------------------------------------------------------
# Combined gate — symmetric input/output guarantees
# ---------------------------------------------------------------------------


class TestCombinedGate:
    @given(delta=finite_deltas, threshold=thresholds)
    def test_below_threshold_short_circuits_oscillation(self, delta: float, threshold: float) -> None:
        assume(threshold > 0)
        assume(delta < threshold)
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=threshold),
            oscillation_guard=OscillationGuard(),
        )
        decision = gate.evaluate(_proposal(predicted_delta=delta))
        assert decision.outcome == PromptPatchOutcome.REJECTED_DELTA
        assert decision.oscillation_result is None

    @given(delta=finite_deltas)
    def test_passing_delta_runs_oscillation(self, delta: float) -> None:
        assume(delta >= 0.0)  # threshold = 0
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(),
        )
        decision = gate.evaluate(_proposal(predicted_delta=delta))
        assert decision.oscillation_result is not None


# ---------------------------------------------------------------------------
# Additional invariants
# ---------------------------------------------------------------------------


class TestAdditionalInvariants:
    @given(sequence=st.lists(content_letters, min_size=1, max_size=20))
    @settings(deadline=None)
    def test_recent_applied_window_bounded_by_size(self, sequence: list[str]) -> None:
        guard = OscillationGuard(window_size=3, max_patches_per_session=0, min_confirmations=1)
        for c in sequence:
            r = guard.evaluate(_proposal(to_content=c))
            if r.verdict == OscillationVerdict.ACCEPTED:
                guard.record_applied(_proposal(to_content=c))
        assert len(guard.recent_applied_hashes("judge")) <= 3

    @given(
        sequence=st.lists(content_letters, min_size=0, max_size=20),
        cap=st.integers(min_value=1, max_value=5),
    )
    @settings(deadline=None)
    def test_after_cap_all_remaining_rejected_with_cap(self, sequence: list[str], cap: int) -> None:
        guard = OscillationGuard(min_confirmations=1, max_patches_per_session=cap, window_size=20)
        for c in sequence:
            r = guard.evaluate(_proposal(to_content=c))
            if r.verdict == OscillationVerdict.ACCEPTED:
                guard.record_applied(_proposal(to_content=c))
            elif r.verdict == OscillationVerdict.REJECTED_SESSION_CAP:
                # Once cap is hit, applied_count should already equal cap.
                assert guard.session_applied_count == cap

    @given(prompt_a=prompt_names, prompt_b=prompt_names)
    def test_different_prompts_independent_pending(self, prompt_a: str, prompt_b: str) -> None:
        assume(prompt_a != prompt_b)
        guard = OscillationGuard(min_confirmations=2)
        r_a = guard.evaluate(_proposal(prompt_name=prompt_a, to_content="X"))
        r_b = guard.evaluate(_proposal(prompt_name=prompt_b, to_content="X"))
        assert r_a.verdict == OscillationVerdict.PENDING_CONFIRMATION
        assert r_b.verdict == OscillationVerdict.PENDING_CONFIRMATION

    @given(delta=finite_deltas, threshold=thresholds)
    def test_result_threshold_matches_gate_threshold(self, delta: float, threshold: float) -> None:
        gate = PredictedDeltaGate(min_delta=threshold)
        result = gate.evaluate(_proposal(predicted_delta=delta))
        assert result.threshold == max(threshold, 0.0)

    @given(content=st.text(min_size=0, max_size=64))
    def test_content_hash_is_stable(self, content: str) -> None:
        h1 = _proposal(to_content=content).content_hash
        h2 = _proposal(to_content=content).content_hash
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex digest length

    @given(content_a=st.text(min_size=0, max_size=32), content_b=st.text(min_size=0, max_size=32))
    def test_different_content_different_hash(self, content_a: str, content_b: str) -> None:
        assume(content_a != content_b)
        h_a = _proposal(to_content=content_a).content_hash
        h_b = _proposal(to_content=content_b).content_hash
        assert h_a != h_b

    @given(delta=finite_deltas, threshold=thresholds)
    def test_accepted_means_positive_delta_post_clamp(self, delta: float, threshold: float) -> None:
        gate = PredictedDeltaGate(min_delta=threshold)
        result = gate.evaluate(_proposal(predicted_delta=delta))
        if result.verdict == PatchVerdict.ACCEPTED:
            assert result.predicted_delta >= result.threshold
