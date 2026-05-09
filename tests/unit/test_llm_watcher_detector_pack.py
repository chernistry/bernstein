"""Tests for the production detector pack on :class:`LLMWatcher`.

The pack ships five plain-Python detectors that fire deterministically
on a frozen :class:`WatcherEvent` snapshot, contributing structured
:class:`Suggestion` records to the watcher output:

* ``cost_runaway_detector``
* ``stuck_spawn_detector``
* ``repeated_failure_detector``
* ``suspicious_tool_mask_detector``
* ``audit_chain_break_detector``

Each detector is independently exercised here with positive (fires) and
negative (no-op) cases, plus an end-to-end integration test that
confirms the five are auto-registered when the watcher is built from
env with ``BERNSTEIN_LLM_WATCHER_ENABLED=1``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bernstein.core.observability.llm_watcher import (
    LLMWatcher,
    Suggestion,
    WatcherConfig,
    WatcherEvent,
    audit_chain_break_detector,
    build_watcher_from_env,
    cost_runaway_detector,
    register_default_detectors,
    repeated_failure_detector,
    stuck_spawn_detector,
    suspicious_tool_mask_detector,
)


def _event(payload: dict[str, Any] | None = None, kind: str = "task_spawned") -> WatcherEvent:
    """Build a frozen WatcherEvent with the given payload."""
    return WatcherEvent(
        kind=kind,  # type: ignore[arg-type]
        run_id="run-detect",
        timestamp=1_700_000_000.0,
        payload=dict(payload or {}),
    )


# ---------------------------------------------------------------------------
# cost_runaway_detector
# ---------------------------------------------------------------------------


class TestCostRunaway:
    """``cost_runaway_detector`` — task >$2 or run >50% budget."""

    def test_fires_on_task_cost_above_hard_ceiling(self) -> None:
        sigs = cost_runaway_detector(
            _event({"task_cost_usd": 2.5, "run_cost_usd": 3.0, "task_id": "T-1"}),
        )
        assert len(sigs) == 1
        assert sigs[0].detector == "cost_runaway"
        assert sigs[0].severity == "critical"
        assert "T-1" in sigs[0].rationale

    def test_fires_on_run_cost_above_half_budget(self) -> None:
        sigs = cost_runaway_detector(
            _event(
                {
                    "task_cost_usd": 0.1,
                    "run_cost_usd": 8.0,
                    "run_budget_usd": 10.0,
                    "task_id": "T-2",
                },
            ),
        )
        assert len(sigs) == 1
        assert sigs[0].severity == "warning"

    def test_silent_when_no_cost_breach(self) -> None:
        sigs = cost_runaway_detector(
            _event(
                {
                    "task_cost_usd": 0.5,
                    "run_cost_usd": 1.0,
                    "run_budget_usd": 10.0,
                },
            ),
        )
        assert sigs == []

    def test_zero_or_missing_budget_does_not_div_by_zero(self) -> None:
        # No budget field — should still fire on task_cost > 2.
        sigs = cost_runaway_detector(_event({"task_cost_usd": 3.0}))
        assert len(sigs) == 1
        # Budget of 0 is treated as no-budget — no run-leg fire.
        sigs = cost_runaway_detector(
            _event({"task_cost_usd": 0.0, "run_cost_usd": 5.0, "run_budget_usd": 0.0}),
        )
        assert sigs == []


# ---------------------------------------------------------------------------
# stuck_spawn_detector
# ---------------------------------------------------------------------------


class TestStuckSpawn:
    """``stuck_spawn_detector`` — claim_confirmed >30 min, no audit."""

    def test_fires_when_claim_confirmed_and_idle_too_long(self) -> None:
        sigs = stuck_spawn_detector(
            _event(
                {
                    "claim_confirmed": True,
                    "task_completed": False,
                    "audit_emissions": 0,
                    "time_in_state_s": 31 * 60,
                    "task_id": "T-9",
                },
            ),
        )
        assert len(sigs) == 1
        assert sigs[0].detector == "stuck_spawn"
        assert sigs[0].severity == "warning"

    def test_silent_when_audit_emissions_present(self) -> None:
        sigs = stuck_spawn_detector(
            _event(
                {
                    "claim_confirmed": True,
                    "audit_emissions": 5,
                    "time_in_state_s": 31 * 60,
                },
            ),
        )
        assert sigs == []

    def test_silent_when_task_completed(self) -> None:
        sigs = stuck_spawn_detector(
            _event(
                {
                    "claim_confirmed": True,
                    "task_completed": True,
                    "time_in_state_s": 99 * 60,
                },
            ),
        )
        assert sigs == []

    def test_silent_when_within_threshold(self) -> None:
        sigs = stuck_spawn_detector(
            _event(
                {
                    "claim_confirmed": True,
                    "task_completed": False,
                    "audit_emissions": 0,
                    "time_in_state_s": 5 * 60,
                },
            ),
        )
        assert sigs == []

    def test_silent_when_claim_not_confirmed(self) -> None:
        sigs = stuck_spawn_detector(
            _event({"claim_confirmed": False, "time_in_state_s": 99 * 60}),
        )
        assert sigs == []


# ---------------------------------------------------------------------------
# repeated_failure_detector
# ---------------------------------------------------------------------------


class TestRepeatedFailure:
    """``repeated_failure_detector`` — same exit signature 3 times."""

    def test_fires_on_three_consecutive_failures(self) -> None:
        sigs = repeated_failure_detector(
            _event(
                {
                    "task_id": "T-7",
                    "failure_count": 3,
                    "exit_signature": "MissingDep:foo",
                },
            ),
        )
        assert len(sigs) == 1
        assert sigs[0].severity == "critical"
        assert "T-7" in sigs[0].rationale
        assert "MissingDep:foo" in sigs[0].rationale

    def test_fires_on_more_than_three(self) -> None:
        sigs = repeated_failure_detector(
            _event({"failure_count": 5, "exit_signature": "OOM"}),
        )
        assert len(sigs) == 1

    def test_silent_below_threshold(self) -> None:
        sigs = repeated_failure_detector(
            _event({"failure_count": 2, "exit_signature": "OOM"}),
        )
        assert sigs == []

    def test_silent_without_exit_signature(self) -> None:
        sigs = repeated_failure_detector(
            _event({"failure_count": 9, "exit_signature": ""}),
        )
        assert sigs == []


# ---------------------------------------------------------------------------
# suspicious_tool_mask_detector
# ---------------------------------------------------------------------------


class TestSuspiciousToolMask:
    """``suspicious_tool_mask_detector`` — masking >50% of tools."""

    def test_fires_when_more_than_half_masked(self) -> None:
        sigs = suspicious_tool_mask_detector(
            _event({"available_tools": 10, "masked_tools": 6}),
        )
        assert len(sigs) == 1
        assert sigs[0].detector == "suspicious_tool_mask"

    def test_silent_when_exactly_half_masked(self) -> None:
        # Boundary — a 50% mask is permitted; anything over fires.
        sigs = suspicious_tool_mask_detector(
            _event({"available_tools": 10, "masked_tools": 5}),
        )
        assert sigs == []

    def test_silent_when_no_tools_available(self) -> None:
        sigs = suspicious_tool_mask_detector(
            _event({"available_tools": 0, "masked_tools": 0}),
        )
        assert sigs == []

    def test_silent_on_below_threshold(self) -> None:
        sigs = suspicious_tool_mask_detector(
            _event({"available_tools": 10, "masked_tools": 1}),
        )
        assert sigs == []


# ---------------------------------------------------------------------------
# audit_chain_break_detector
# ---------------------------------------------------------------------------


class TestAuditChainBreak:
    """``audit_chain_break_detector`` — prev_hmac mismatch on append."""

    def test_fires_on_mismatch(self) -> None:
        sigs = audit_chain_break_detector(
            _event(
                {
                    "prev_hmac": "a" * 64,
                    "expected_prev_hmac": "b" * 64,
                },
            ),
        )
        assert len(sigs) == 1
        assert sigs[0].severity == "critical"
        assert sigs[0].detector == "audit_chain_break"

    def test_silent_on_match(self) -> None:
        sigs = audit_chain_break_detector(
            _event(
                {
                    "prev_hmac": "deadbeef" * 8,
                    "expected_prev_hmac": "deadbeef" * 8,
                },
            ),
        )
        assert sigs == []

    def test_silent_when_prev_missing(self) -> None:
        sigs = audit_chain_break_detector(
            _event({"expected_prev_hmac": "x" * 64}),
        )
        assert sigs == []

    def test_silent_when_expected_missing(self) -> None:
        sigs = audit_chain_break_detector(_event({"prev_hmac": "x" * 64}))
        assert sigs == []


# ---------------------------------------------------------------------------
# build_watcher_from_env wires the pack
# ---------------------------------------------------------------------------


class TestRegistration:
    """Auto-registration via build_watcher_from_env when watcher is enabled."""

    def test_disabled_watcher_has_no_detectors_registered(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BERNSTEIN_LLM_WATCHER_ENABLED", raising=False)
        watcher = build_watcher_from_env()
        # Off-by-default contract: no detectors on a disabled watcher.
        assert watcher.detectors == ()

    def test_enabled_watcher_registers_all_five_detectors(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BERNSTEIN_LLM_WATCHER_ENABLED", "1")
        watcher = build_watcher_from_env()
        names = [getattr(d, "__name__", "?") for d in watcher.detectors]
        assert names == [
            "cost_runaway_detector",
            "stuck_spawn_detector",
            "repeated_failure_detector",
            "suspicious_tool_mask_detector",
            "audit_chain_break_detector",
        ]

    def test_register_default_detectors_is_idempotent_per_call(self) -> None:
        watcher = LLMWatcher(WatcherConfig(enabled=True))
        register_default_detectors(watcher)
        first = len(watcher.detectors)
        # A second call duplicates registrations (caller responsibility).
        # The contract: register_default_detectors is a single-shot helper.
        register_default_detectors(watcher)
        assert len(watcher.detectors) == 2 * first

    def test_observe_runs_detectors_then_llm(self) -> None:
        """End-to-end: a single event triggers detectors + LLM observer."""
        captured: list[tuple[Any, Any]] = []

        async def caller(*args: Any, **kwargs: Any) -> str:
            captured.append((args, kwargs))
            return "advisory text"

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        register_default_detectors(watcher)

        event = _event(
            {
                "task_id": "T-1",
                "task_cost_usd": 3.0,  # triggers cost_runaway
                "failure_count": 3,
                "exit_signature": "OOM",  # triggers repeated_failure
            },
        )

        signals = asyncio.run(watcher.observe(event))
        # 2 detectors + 1 LLM observer = 3 suggestions.
        assert len(signals) == 3
        detectors = {s.detector for s in signals}
        assert {"cost_runaway", "repeated_failure", "observer"} <= detectors
        assert all(isinstance(s, Suggestion) for s in signals)

    def test_disabled_watcher_skips_detectors(self) -> None:
        """A disabled watcher must not even invoke its registered detectors."""
        invoked: list[str] = []

        def loud_detector(event: WatcherEvent) -> list[Suggestion]:
            invoked.append("called")
            return []

        watcher = LLMWatcher(WatcherConfig(enabled=False))
        watcher.register_detector(loud_detector)
        signals = asyncio.run(watcher.observe(_event()))
        assert signals == []
        assert invoked == []

    def test_failing_detector_does_not_block_peers(self) -> None:
        """One detector raising must not stop other detectors / LLM observer."""

        def boom(event: WatcherEvent) -> list[Suggestion]:
            raise RuntimeError("kaboom")

        async def caller(*_a: Any, **_kw: Any) -> str:
            return "advisory"

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        watcher.register_detector(boom)
        watcher.register_detector(cost_runaway_detector)

        signals = asyncio.run(
            watcher.observe(_event({"task_cost_usd": 5.0, "task_id": "T-x"})),
        )
        # cost_runaway fires + LLM observer fires = 2 signals.
        assert len(signals) == 2
        assert {s.detector for s in signals} == {"cost_runaway", "observer"}
