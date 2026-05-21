"""Gap-filling behavioral tests for the smaller agent modules.

Targets dark branches in three already-partially-covered modules:

* ``agent_cost_ledger`` - infinite cost-per-task efficiency, the
  ``cost_per_task=None`` serialisation branch, the no-op when recording a
  result for an unknown agent, and ``LeaderboardEntry`` serialisation.
* ``spawn_supervisor`` - unknown-session queries, ``forget``, and the
  resume-non-parked path.
* ``agent_session_token_breakdown`` - the cache / optimization-note
  rendering in ``summary``, the >55% context note, and the malformed /
  missing prompt-report branches.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.agents.agent_cost_ledger import (
    AgentCostEntry,
    AgentCostLedger,
    LeaderboardEntry,
)
from bernstein.core.agents.agent_session_token_breakdown import (
    AgentSessionTokenBreakdown,
    _load_prompt_token_report,
    load_session_breakdown,
)
from bernstein.core.agents.spawn_supervisor import RespawnBudget, SpawnSupervisor, SupervisorState

# ---------------------------------------------------------------------------
# agent_cost_ledger
# ---------------------------------------------------------------------------


def test_efficiency_score_zero_when_no_completions() -> None:
    """An entry with cost but no completions has an infinite cost-per-task
    and therefore a zero efficiency score."""
    entry = AgentCostEntry(agent_id="a", role="backend", model="m", total_cost_usd=0.1, tasks_completed=0)
    assert entry.efficiency_score == 0.0


def test_to_dict_cost_per_task_none_without_completions() -> None:
    """to_dict reports cost_per_task as None when no tasks completed."""
    entry = AgentCostEntry(agent_id="a", role="backend", model="m", total_cost_usd=0.1)
    assert entry.to_dict()["cost_per_task"] is None


def test_record_task_result_unknown_agent_is_noop() -> None:
    """Recording a result for an unrecorded agent creates no entry."""
    ledger = AgentCostLedger("r1")
    ledger.record_task_result("ghost", success=True, duration_s=5.0)
    assert ledger.get_entry("ghost") is None


def test_record_task_result_accumulates_duration() -> None:
    """Successful results accumulate completed count and duration."""
    ledger = AgentCostLedger("r1")
    ledger.record_cost("a1", role="backend", model="m", cost_usd=0.05)
    ledger.record_task_result("a1", success=True, duration_s=10.0)
    ledger.record_task_result("a1", success=True, duration_s=5.0)
    entry = ledger.get_entry("a1")
    assert entry is not None
    assert entry.tasks_completed == 2
    assert entry.total_duration_s == pytest.approx(15.0)


def test_leaderboard_min_tasks_excludes_low_completion() -> None:
    """min_tasks filters out agents below the completion floor."""
    ledger = AgentCostLedger("r2")
    ledger.record_cost("a1", role="backend", model="m", cost_usd=0.05)
    ledger.record_task_result("a1", success=True)  # only 1 completion
    assert ledger.leaderboard(min_tasks=2) == []


def test_leaderboard_entry_to_dict_rounds_fields() -> None:
    """LeaderboardEntry.to_dict carries rank and rounded metrics."""
    entry = LeaderboardEntry(
        rank=2,
        agent_id="agent-x",
        role="qa",
        model="haiku",
        cost_per_task=0.05,
        success_rate=0.75,
        efficiency_score=14.9,
        total_cost_usd=0.5,
    )
    d = entry.to_dict()
    assert d["rank"] == 2
    assert d["agent_id"] == "agent-x"
    assert d["role"] == "qa"
    assert d["cost_per_task"] == pytest.approx(0.05)
    assert d["success_rate"] == pytest.approx(0.75)
    assert d["efficiency_score"] == pytest.approx(14.9)


def test_record_cost_accumulates_tokens() -> None:
    """Repeated cost records accumulate input/output tokens on one entry."""
    ledger = AgentCostLedger("r3")
    ledger.record_cost("a1", role="backend", model="m", cost_usd=0.01, input_tokens=100, output_tokens=20)
    ledger.record_cost("a1", role="backend", model="m", cost_usd=0.02, input_tokens=50, output_tokens=10)
    entry = ledger.get_entry("a1")
    assert entry is not None
    assert entry.total_input_tokens == 150
    assert entry.total_output_tokens == 30


# ---------------------------------------------------------------------------
# spawn_supervisor - minor query / control paths
# ---------------------------------------------------------------------------


def test_state_unknown_session_is_healthy() -> None:
    """An unknown session reports HEALTHY and not parked."""
    sup = SpawnSupervisor()
    assert sup.state("nope") == SupervisorState.HEALTHY
    assert sup.is_parked("nope") is False
    assert sup.respawns_in_window("nope") == 0


def test_forget_removes_session_state() -> None:
    """forget drops all supervision state, reverting to defaults."""
    sup = SpawnSupervisor()
    sup.spawn("s1", lambda: "ok")
    sup.forget("s1")
    # No record remains, so state falls back to the HEALTHY default.
    assert sup.state("s1") == SupervisorState.HEALTHY


def test_forget_unknown_session_is_safe() -> None:
    """Forgetting an unknown session does not raise."""
    SpawnSupervisor().forget("never-seen")


def test_resume_non_parked_clears_respawn_window() -> None:
    """Resuming a healthy-but-flaky session clears its respawn window."""
    sup = SpawnSupervisor(RespawnBudget(max_respawns=3, initial_backoff_ms=0, max_backoff_ms=0))
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "done"

    sup.spawn("s2", flaky)
    assert sup.respawns_in_window("s2") == 1
    assert sup.resume("s2") is True
    assert sup.respawns_in_window("s2") == 0
    assert sup.is_parked("s2") is False


# ---------------------------------------------------------------------------
# agent_session_token_breakdown - summary + load branches
# ---------------------------------------------------------------------------


def test_summary_includes_cache_and_notes() -> None:
    """summary renders the cache line and optimization notes when present."""
    bd = AgentSessionTokenBreakdown(
        session_id="S",
        model="opus",
        actual_input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=500,
        cache_write_tokens=100,
        optimization_notes=["trim context"],
    )
    summary = bd.summary()
    assert "Cache: read 500" in summary
    assert "Optimization notes:" in summary
    assert "trim context" in summary


def test_total_tokens_is_input_plus_output() -> None:
    """total_tokens sums actual input and output."""
    bd = AgentSessionTokenBreakdown(session_id="S", actual_input_tokens=1000, output_tokens=200)
    assert bd.total_tokens == 1200


def test_percentages_zero_when_no_tokens() -> None:
    """percentages are all zero when no tokens were consumed."""
    pct = AgentSessionTokenBreakdown(session_id="E").percentages()
    assert pct == {
        "system_prompt": 0.0,
        "context": 0.0,
        "user_prompt": 0.0,
        "tool_results": 0.0,
        "output": 0.0,
    }


def test_load_session_breakdown_flags_large_context(tmp_path: Path) -> None:
    """A context section exceeding 55% of input tokens emits a trim note."""
    (tmp_path / "metrics").mkdir(parents=True)
    report = {
        "system_prompt_tokens": 100,
        "context_tokens": 700,
        "user_prompt_tokens": 50,
        "suggestions": ["reduce files"],
    }
    (tmp_path / "metrics" / "prompt_token_usage_SX.json").write_text(json.dumps(report), encoding="utf-8")
    bd = load_session_breakdown(tmp_path, "SX", actual_input_tokens=1000, actual_output_tokens=100)
    assert any("Context sections are" in note for note in bd.optimization_notes)
    # Carried-over suggestion from the report.
    assert "reduce files" in bd.optimization_notes
    # Tool-result tokens = max(0, actual_input - estimated_total) = 1000 - 850.
    assert bd.tool_result_tokens == 150


def test_load_session_breakdown_no_analysis_note(tmp_path: Path) -> None:
    """With no prompt report but real input, a missing-analysis note is added."""
    (tmp_path / "metrics").mkdir(parents=True)
    bd = load_session_breakdown(tmp_path, "NOANALYSIS", actual_input_tokens=500)
    assert any("No pre-session prompt analysis" in note for note in bd.optimization_notes)


def test_load_prompt_token_report_malformed_returns_none(tmp_path: Path) -> None:
    """A corrupt prompt-report JSON file yields None, not an exception."""
    (tmp_path / "metrics").mkdir(parents=True)
    (tmp_path / "metrics" / "prompt_token_usage_BAD.json").write_text("{not valid", encoding="utf-8")
    assert _load_prompt_token_report(tmp_path, "BAD") is None


def test_load_prompt_token_report_missing_returns_none(tmp_path: Path) -> None:
    """A missing prompt-report file yields None."""
    (tmp_path / "metrics").mkdir(parents=True)
    assert _load_prompt_token_report(tmp_path, "NOPE") is None
