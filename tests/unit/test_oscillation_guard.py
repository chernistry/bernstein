"""Unit tests for the oscillation guard.

Covers the acceptance criteria from issue #1348:
- Single-cycle proposals stay pending.
- Two consecutive same-hash proposals confirm and accept.
- Different hashes between cycles do not confirm.
- Flip-back (A → B → A) is vetoed.
- Per-session cap is enforced.
- Audit row format from :class:`PromptPatchGate` matches the spec.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bernstein.evolution.gate import (
    PromptPatchDecision,
    PromptPatchGate,
    PromptPatchOutcome,
)
from bernstein.evolution.oscillation_guard import (
    DEFAULT_MAX_PATCHES_PER_SESSION,
    DEFAULT_WINDOW_SIZE,
    OscillationGuard,
    OscillationVerdict,
    resolve_max_patches_per_session,
)
from bernstein.evolution.predicted_delta import (
    PatchProposal,
    PredictedDeltaGate,
)


def _make(
    *,
    prompt_name: str = "judge",
    from_version_id: str = "v1",
    to_content: str = "A",
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
# resolve_max_patches_per_session
# ---------------------------------------------------------------------------


class TestResolveMax:
    def test_default(self) -> None:
        assert resolve_max_patches_per_session() == DEFAULT_MAX_PATCHES_PER_SESSION

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", "10")
        assert resolve_max_patches_per_session() == 10

    def test_env_garbage_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", "abc")
        assert resolve_max_patches_per_session() == DEFAULT_MAX_PATCHES_PER_SESSION

    def test_env_zero_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", "0")
        assert resolve_max_patches_per_session() == 0

    def test_env_negative_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", "-3")
        assert resolve_max_patches_per_session() == 0


# ---------------------------------------------------------------------------
# OscillationGuard — consecutive-cycle confirmation
# ---------------------------------------------------------------------------


class TestConsecutiveCycleConfirmation:
    def test_first_cycle_pending(self) -> None:
        guard = OscillationGuard()
        result = guard.evaluate(_make(to_content="A"))
        assert result.verdict == OscillationVerdict.PENDING_CONFIRMATION
        assert result.confirmations == 1

    def test_second_cycle_accepts(self) -> None:
        guard = OscillationGuard()
        guard.evaluate(_make(to_content="A"))
        result = guard.evaluate(_make(to_content="A"))
        assert result.verdict == OscillationVerdict.ACCEPTED
        assert result.confirmations == 2

    def test_different_content_resets_confirmation(self) -> None:
        guard = OscillationGuard()
        guard.evaluate(_make(to_content="A"))
        # Different content — new pending track.
        result_b = guard.evaluate(_make(to_content="B"))
        assert result_b.verdict == OscillationVerdict.PENDING_CONFIRMATION
        # A's pending state has not advanced.
        result_a2 = guard.evaluate(_make(to_content="A"))
        assert result_a2.verdict == OscillationVerdict.ACCEPTED

    def test_min_confirmations_one_accepts_immediately(self) -> None:
        guard = OscillationGuard(min_confirmations=1)
        result = guard.evaluate(_make(to_content="A"))
        assert result.verdict == OscillationVerdict.ACCEPTED

    def test_min_confirmations_three(self) -> None:
        guard = OscillationGuard(min_confirmations=3)
        r1 = guard.evaluate(_make(to_content="A"))
        r2 = guard.evaluate(_make(to_content="A"))
        r3 = guard.evaluate(_make(to_content="A"))
        assert r1.verdict == OscillationVerdict.PENDING_CONFIRMATION
        assert r2.verdict == OscillationVerdict.PENDING_CONFIRMATION
        assert r3.verdict == OscillationVerdict.ACCEPTED

    def test_pending_per_prompt_independent(self) -> None:
        guard = OscillationGuard()
        guard.evaluate(_make(prompt_name="judge", to_content="A"))
        result = guard.evaluate(_make(prompt_name="manager", to_content="A"))
        # Different prompts, same content — manager is fresh pending.
        assert result.verdict == OscillationVerdict.PENDING_CONFIRMATION

    def test_pending_count_tracked(self) -> None:
        guard = OscillationGuard()
        assert guard.pending_count() == 0
        guard.evaluate(_make(to_content="A"))
        guard.evaluate(_make(to_content="B"))
        assert guard.pending_count() == 2

    def test_pending_cleared_on_apply(self) -> None:
        guard = OscillationGuard()
        guard.evaluate(_make(to_content="A"))
        guard.evaluate(_make(to_content="A"))
        guard.record_applied(_make(to_content="A"))
        # The (prompt_name, content_hash) entry should be gone.
        assert guard.pending_count() == 0


# ---------------------------------------------------------------------------
# Flip-back detection
# ---------------------------------------------------------------------------


class TestFlipBack:
    def test_simple_a_b_a_is_flip_back(self) -> None:
        guard = OscillationGuard()
        guard.record_applied(_make(to_content="A"))
        guard.record_applied(_make(to_content="B"))
        result = guard.evaluate(_make(to_content="A"))
        assert result.verdict == OscillationVerdict.REJECTED_FLIP_BACK
        assert "flip_back" in result.reason

    def test_a_b_c_b_a_is_flip_back_for_a(self) -> None:
        guard = OscillationGuard(window_size=5)
        for c in ("A", "B", "C", "B"):
            guard.record_applied(_make(to_content=c))
        result = guard.evaluate(_make(to_content="A"))
        assert result.verdict == OscillationVerdict.REJECTED_FLIP_BACK

    def test_re_apply_same_content_is_not_flip_back(self) -> None:
        guard = OscillationGuard()
        guard.record_applied(_make(to_content="A"))
        # Proposing A again with no B in between is a re-apply / no-op.
        result = guard.evaluate(_make(to_content="A"))
        assert result.verdict != OscillationVerdict.REJECTED_FLIP_BACK

    def test_flip_back_only_within_window(self) -> None:
        guard = OscillationGuard(window_size=2)
        guard.record_applied(_make(to_content="A"))
        guard.record_applied(_make(to_content="B"))
        guard.record_applied(_make(to_content="C"))  # evicts A
        result = guard.evaluate(_make(to_content="A"))
        # A is no longer in the window — A→B→C→A is not a "flip back to A"
        # because A's history was forgotten.
        assert result.verdict != OscillationVerdict.REJECTED_FLIP_BACK

    def test_flip_back_clears_pending(self) -> None:
        guard = OscillationGuard()
        guard.record_applied(_make(to_content="A"))
        guard.record_applied(_make(to_content="B"))
        guard.evaluate(_make(to_content="A"))
        # After veto, no pending entry should linger.
        assert guard.pending_count() == 0

    def test_flip_back_only_within_same_prompt(self) -> None:
        guard = OscillationGuard()
        guard.record_applied(_make(prompt_name="judge", to_content="A"))
        guard.record_applied(_make(prompt_name="judge", to_content="B"))
        # Same content but different prompt — not a flip-back.
        result = guard.evaluate(_make(prompt_name="manager", to_content="A"))
        assert result.verdict != OscillationVerdict.REJECTED_FLIP_BACK


# ---------------------------------------------------------------------------
# Session cap
# ---------------------------------------------------------------------------


class TestSessionCap:
    def test_default_cap_three(self) -> None:
        guard = OscillationGuard(min_confirmations=1)
        for i, c in enumerate(("A", "B", "C")):
            r = guard.evaluate(_make(to_content=c))
            assert r.verdict == OscillationVerdict.ACCEPTED, f"#{i}"
            guard.record_applied(_make(to_content=c))
        # 4th — capped.
        r4 = guard.evaluate(_make(to_content="D"))
        assert r4.verdict == OscillationVerdict.REJECTED_SESSION_CAP
        assert "session_cap" in r4.reason

    def test_session_cap_zero_disables(self) -> None:
        guard = OscillationGuard(min_confirmations=1, max_patches_per_session=0)
        for c in "ABCDE":
            r = guard.evaluate(_make(to_content=c))
            assert r.verdict == OscillationVerdict.ACCEPTED
            guard.record_applied(_make(to_content=c))

    def test_session_cap_custom(self) -> None:
        guard = OscillationGuard(min_confirmations=1, max_patches_per_session=2)
        for c in "AB":
            guard.evaluate(_make(to_content=c))
            guard.record_applied(_make(to_content=c))
        r = guard.evaluate(_make(to_content="C"))
        assert r.verdict == OscillationVerdict.REJECTED_SESSION_CAP

    def test_session_cap_resolved_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", "5")
        guard = OscillationGuard(min_confirmations=1)
        assert guard.max_patches_per_session == 5

    def test_session_reset(self) -> None:
        guard = OscillationGuard(min_confirmations=1)
        for c in "ABC":
            guard.evaluate(_make(to_content=c))
            guard.record_applied(_make(to_content=c))
        assert guard.session_applied_count == 3
        guard.reset_session()
        assert guard.session_applied_count == 0
        # New patch after reset works.
        r = guard.evaluate(_make(to_content="D"))
        assert r.verdict == OscillationVerdict.ACCEPTED


# ---------------------------------------------------------------------------
# record_applied
# ---------------------------------------------------------------------------


class TestRecordApplied:
    def test_idempotent_for_same_content(self) -> None:
        guard = OscillationGuard()
        guard.record_applied(_make(to_content="A"))
        guard.record_applied(_make(to_content="A"))
        assert guard.session_applied_count == 1

    def test_increments_for_different_content(self) -> None:
        guard = OscillationGuard()
        guard.record_applied(_make(to_content="A"))
        guard.record_applied(_make(to_content="B"))
        assert guard.session_applied_count == 2

    def test_recent_applied_hashes(self) -> None:
        guard = OscillationGuard(window_size=3)
        for c in "ABCD":
            guard.record_applied(_make(to_content=c))
        hashes = guard.recent_applied_hashes("judge")
        # window size 3 → only last 3 retained.
        assert len(hashes) == 3

    def test_window_size_one_keeps_only_latest(self) -> None:
        guard = OscillationGuard(window_size=1)
        for c in "ABCD":
            guard.record_applied(_make(to_content=c))
        hashes = guard.recent_applied_hashes("judge")
        assert len(hashes) == 1

    def test_recent_hashes_empty_for_unknown_prompt(self) -> None:
        guard = OscillationGuard()
        assert guard.recent_applied_hashes("nonexistent") == []


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_round_trip_serialisable(self) -> None:
        import json

        guard = OscillationGuard()
        guard.evaluate(_make(to_content="A"))
        guard.record_applied(_make(to_content="B"))
        snap = guard.snapshot()
        # Must be JSON-serialisable.
        encoded = json.dumps(snap)
        assert "session_applied_count" in encoded
        assert "applied" in snap
        assert "pending" in snap

    def test_snapshot_records_window(self) -> None:
        guard = OscillationGuard(window_size=5)
        snap = guard.snapshot()
        assert snap["window_size"] == 5

    def test_default_window_size(self) -> None:
        guard = OscillationGuard()
        assert guard.window_size == DEFAULT_WINDOW_SIZE


# ---------------------------------------------------------------------------
# PromptPatchGate wiring (combines delta + oscillation)
# ---------------------------------------------------------------------------


class TestPromptPatchGate:
    def test_delta_failure_short_circuits(self, tmp_path: Path) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
            session_id="s1",
        )
        decision = gate.evaluate(_make(predicted_delta=0.02))
        assert decision.outcome == PromptPatchOutcome.REJECTED_DELTA
        assert not decision.applied
        assert decision.oscillation_result is None
        assert "below_threshold" in decision.veto_message

    def test_oscillation_pending_after_delta_pass(self, tmp_path: Path) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(),
            audit_dir=tmp_path,
        )
        decision = gate.evaluate(_make(predicted_delta=0.10))
        assert decision.outcome == PromptPatchOutcome.PENDING
        assert not decision.applied
        assert decision.oscillation_result is not None

    def test_two_cycles_applied(self, tmp_path: Path) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(),
            audit_dir=tmp_path,
        )
        gate.evaluate(_make(predicted_delta=0.10))
        decision = gate.evaluate(_make(predicted_delta=0.10))
        assert decision.outcome == PromptPatchOutcome.APPLIED
        assert decision.applied

    def test_session_cap_after_three(self, tmp_path: Path) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
        )
        for c in "ABC":
            d = gate.evaluate(_make(to_content=c, predicted_delta=0.10))
            assert d.outcome == PromptPatchOutcome.APPLIED
            gate.mark_applied(_make(to_content=c, predicted_delta=0.10))
        d4 = gate.evaluate(_make(to_content="D", predicted_delta=0.10))
        assert d4.outcome == PromptPatchOutcome.REJECTED_SESSION_CAP
        assert "session_cap" in d4.veto_message

    def test_flip_back_through_combined_gate(self, tmp_path: Path) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
        )
        gate.evaluate(_make(to_content="A", predicted_delta=0.10))
        gate.mark_applied(_make(to_content="A", predicted_delta=0.10))
        gate.evaluate(_make(to_content="B", predicted_delta=0.10))
        gate.mark_applied(_make(to_content="B", predicted_delta=0.10))
        decision = gate.evaluate(_make(to_content="A", predicted_delta=0.10))
        assert decision.outcome == PromptPatchOutcome.REJECTED_OSCILLATION

    def test_delta_zero_disables_only_delta_check(self, tmp_path: Path) -> None:
        """AC: BERNSTEIN_PROMPT_MIN_DELTA=0 disables only the delta check; oscillation still applies."""
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(),
            audit_dir=tmp_path,
        )
        # Even with delta = 0 (or slightly positive), oscillation guard
        # demands a second confirmation.
        d1 = gate.evaluate(_make(predicted_delta=0.0))
        assert d1.outcome == PromptPatchOutcome.PENDING

    def test_audit_row_is_written(self, tmp_path: Path) -> None:
        import json

        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
            session_id="audit-test",
        )
        gate.evaluate(_make(predicted_delta=0.02))
        gate.evaluate(_make(to_content="B", predicted_delta=0.10))
        log_path = tmp_path / "audit-test.jsonl"
        assert log_path.exists()
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert len(rows) == 2
        assert rows[0]["outcome"] == "below_threshold"
        assert rows[0]["applied"] is False
        assert rows[1]["outcome"] == "applied"
        assert rows[1]["applied"] is True

    def test_audit_skipped_when_no_dir(self) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
        )
        gate.evaluate(_make(predicted_delta=0.10))
        assert gate.audit_path is None

    def test_audit_row_carries_full_payload(self, tmp_path: Path) -> None:
        import json

        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
        )
        gate.evaluate(_make(predicted_delta=0.10))
        rows = [json.loads(line) for line in (tmp_path / "default.jsonl").read_text().splitlines()]
        row = rows[0]
        assert "delta" in row
        assert "oscillation" in row
        assert "veto_message" in row
        assert row["delta"]["threshold"] == 0.05
        assert row["delta"]["verdict"] == "accepted"


# ---------------------------------------------------------------------------
# Snapshot test of the veto message format
# ---------------------------------------------------------------------------


class TestVetoMessageSnapshot:
    def test_below_threshold_format(self) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
        )
        proposal = PatchProposal(
            prompt_name="judge",
            from_version_id="v1",
            to_content="content",
            rationale="r",
            predicted_delta=0.01,
            proposal_id="abc123",
        )
        decision = gate.evaluate(proposal)
        assert decision.veto_message == (
            "[prompt-patch-gate] prompt=judge proposal_id=abc123 "
            "verdict=below_threshold reason=below_threshold: "
            "predicted_delta=0.0100 < threshold=0.0500 (source=proposer)"
        )

    def test_flip_back_format(self) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
        )
        a = PatchProposal(
            prompt_name="judge",
            from_version_id="v1",
            to_content="A",
            rationale="r",
            predicted_delta=0.10,
            proposal_id="aaa",
        )
        b = PatchProposal(
            prompt_name="judge",
            from_version_id="v1",
            to_content="B",
            rationale="r",
            predicted_delta=0.10,
            proposal_id="bbb",
        )
        gate.evaluate(a)
        gate.mark_applied(a)
        gate.evaluate(b)
        gate.mark_applied(b)
        flip = gate.evaluate(a)
        assert flip.veto_message.startswith("[prompt-patch-gate] prompt=judge proposal_id=aaa verdict=flip_back")

    def test_session_cap_format(self) -> None:
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1, max_patches_per_session=1),
        )
        a = _make(to_content="A", predicted_delta=0.10)
        b = _make(to_content="B", predicted_delta=0.10)
        gate.evaluate(a)
        gate.mark_applied(a)
        decision = gate.evaluate(b)
        assert decision.outcome == PromptPatchOutcome.REJECTED_SESSION_CAP
        assert "verdict=session_cap" in decision.veto_message
        assert "already applied 1 of 1 allowed patches" in decision.veto_message


# ---------------------------------------------------------------------------
# Decision serialisation
# ---------------------------------------------------------------------------


class TestPromptPatchDecisionSerialisation:
    def test_to_dict_round_trips_via_json(self, tmp_path: Path) -> None:
        import json

        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
        )
        decision: PromptPatchDecision = gate.evaluate(_make(predicted_delta=0.10))
        encoded = json.dumps(decision.to_dict())
        decoded = json.loads(encoded)
        assert decoded["proposal_id"] == decision.proposal_id
        assert decoded["outcome"] == "applied"
        assert decoded["applied"] is True
