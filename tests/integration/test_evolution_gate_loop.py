"""End-to-end integration tests for the prompt-patch evolution gate.

These tests exercise the full evolution-cycle wiring with the
:class:`PromptPatchGate` enabled vs disabled. They are kept lightweight
(no real LLM calls, no real prompt template files) — the goal is to
verify the gate's effect on the proposal stream, audit log, and applied
patches, not to re-test individual evolution components.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bernstein.evolution.gate import (
    PromptPatchGate,
    PromptPatchOutcome,
)
from bernstein.evolution.oscillation_guard import OscillationGuard
from bernstein.evolution.predicted_delta import (
    PatchProposal,
    PredictedDeltaGate,
)

# ---------------------------------------------------------------------------
# Minimal in-memory evolution-cycle harness
# ---------------------------------------------------------------------------


@dataclass
class FakePromptStore:
    """Records *applied* prompts so the tests can assert on final state."""

    history: list[tuple[str, str, str]] = field(default_factory=list)

    def apply(self, prompt_name: str, from_version_id: str, content: str) -> None:
        self.history.append((prompt_name, from_version_id, content))

    def current(self, prompt_name: str) -> str | None:
        for name, _, content in reversed(self.history):
            if name == prompt_name:
                return content
        return None


@dataclass
class FakeEvolutionLoop:
    """A toy evolution loop that submits one proposal per cycle.

    With ``gate`` provided, each proposal goes through the predicted-delta
    + oscillation guards. With ``gate=None``, every proposal is applied
    unconditionally — mirroring the *pre-#1348* behaviour.
    """

    store: FakePromptStore
    gate: PromptPatchGate | None
    applied: list[PatchProposal] = field(default_factory=list)
    decisions: list[object] = field(default_factory=list)

    def run_cycle(self, proposal: PatchProposal) -> None:
        if self.gate is None:
            self._apply(proposal)
            return
        decision = self.gate.evaluate(proposal)
        self.decisions.append(decision)
        if decision.outcome == PromptPatchOutcome.APPLIED:
            self._apply(proposal)
            self.gate.mark_applied(proposal)

    def _apply(self, proposal: PatchProposal) -> None:
        self.applied.append(proposal)
        self.store.apply(
            proposal.prompt_name,
            proposal.from_version_id,
            proposal.to_content,
        )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("BERNSTEIN_PROMPT_MIN_DELTA", raising=False)
    monkeypatch.delenv("BERNSTEIN_PROMPT_MAX_PATCHES_PER_SESSION", raising=False)
    yield


def _proposal(
    *,
    to_content: str,
    predicted_delta: float = 0.10,
    prompt_name: str = "judge",
    from_version_id: str = "v1",
    rationale: str = "tighter",
) -> PatchProposal:
    return PatchProposal(
        prompt_name=prompt_name,
        from_version_id=from_version_id,
        to_content=to_content,
        rationale=rationale,
        predicted_delta=predicted_delta,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvolutionLoopWithGate:
    def test_gate_disabled_applies_everything(self) -> None:
        """Baseline: without the gate, every proposal is applied (legacy behaviour)."""
        store = FakePromptStore()
        loop = FakeEvolutionLoop(store=store, gate=None)
        for c in "ABCDEFG":
            loop.run_cycle(_proposal(to_content=c))
        # All 7 proposals applied, no decisions recorded.
        assert len(loop.applied) == 7
        assert loop.decisions == []
        # Final state is the last applied content.
        assert store.current("judge") == "G"

    def test_gate_enabled_blocks_low_delta(self, tmp_path: Path) -> None:
        store = FakePromptStore()
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
            session_id="lowdelta",
        )
        loop = FakeEvolutionLoop(store=store, gate=gate)
        loop.run_cycle(_proposal(to_content="bad", predicted_delta=0.01))
        loop.run_cycle(_proposal(to_content="good", predicted_delta=0.10))
        assert len(loop.applied) == 1
        assert loop.applied[0].to_content == "good"

        # Audit log captures the rejected proposal.
        log = tmp_path / "lowdelta.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert rows[0]["outcome"] == "below_threshold"
        assert rows[1]["outcome"] == "applied"

    def test_gate_enabled_blocks_flip_back(self, tmp_path: Path) -> None:
        store = FakePromptStore()
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
        )
        loop = FakeEvolutionLoop(store=store, gate=gate)
        loop.run_cycle(_proposal(to_content="A"))
        loop.run_cycle(_proposal(to_content="B"))
        # A → B → A should be vetoed by the oscillation guard.
        loop.run_cycle(_proposal(to_content="A"))
        contents = [p.to_content for p in loop.applied]
        assert contents == ["A", "B"]
        assert store.current("judge") == "B"

    def test_gate_enabled_enforces_session_cap(self, tmp_path: Path) -> None:
        store = FakePromptStore()
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(min_confirmations=1, max_patches_per_session=3),
            audit_dir=tmp_path,
        )
        loop = FakeEvolutionLoop(store=store, gate=gate)
        for c in "ABCDE":
            loop.run_cycle(_proposal(to_content=c, predicted_delta=0.10))
        # Only 3 applied because of the cap.
        assert len(loop.applied) == 3
        assert [p.to_content for p in loop.applied] == ["A", "B", "C"]

    def test_two_consecutive_cycle_confirmation(self, tmp_path: Path) -> None:
        """AC: same patch hash in two consecutive cycles is accepted on cycle N+1."""
        store = FakePromptStore()
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.05),
            oscillation_guard=OscillationGuard(min_confirmations=2),
            audit_dir=tmp_path,
        )
        loop = FakeEvolutionLoop(store=store, gate=gate)
        # Cycle N: same proposal, predicted_delta clears threshold.
        loop.run_cycle(_proposal(to_content="A", predicted_delta=0.10))
        assert len(loop.applied) == 0  # pending
        # Cycle N+1: same proposal again — confirmed and applied.
        loop.run_cycle(_proposal(to_content="A", predicted_delta=0.10))
        assert len(loop.applied) == 1
        assert store.current("judge") == "A"


class TestZeroThresholdSemantics:
    def test_zero_threshold_disables_only_delta_check(self, tmp_path: Path) -> None:
        """AC: BERNSTEIN_PROMPT_MIN_DELTA=0 disables delta check; oscillation guard still applies."""
        store = FakePromptStore()
        gate = PromptPatchGate(
            delta_gate=PredictedDeltaGate(min_delta=0.0),
            oscillation_guard=OscillationGuard(min_confirmations=1),
            audit_dir=tmp_path,
        )
        loop = FakeEvolutionLoop(store=store, gate=gate)
        loop.run_cycle(_proposal(to_content="A", predicted_delta=0.0))
        loop.run_cycle(_proposal(to_content="B", predicted_delta=0.0))
        # Both applied, oscillation guard sees them.
        loop.run_cycle(_proposal(to_content="A", predicted_delta=0.0))
        # Third should be flip-back-vetoed.
        contents = [p.to_content for p in loop.applied]
        assert contents == ["A", "B"]
