"""Tests for the cost autopilot per-run budget cap (--max-cost-usd).

Covers:
  * ``resolve_run_budget_usd`` precedence (env > run_config > seed > default).
  * Invalid env values fall through to lower layers without raising.
  * ``CostTracker.can_spawn`` enforces the resolved cap (cap-enforcement).
  * Sub-cap runs are unaffected (no false positives).
  * ``BudgetStatus.should_stop`` flips at the hard-stop threshold.

The CLI flag (``bernstein run --max-cost-usd N``) is a thin propagation
layer that sets the env var via :func:`_propagate_env_flags`; behaviour is
exercised end-to-end through the resolver.
"""

from __future__ import annotations

import pytest
from bernstein.core.cost_tracker import (
    ENV_MAX_COST_USD,
    CostTracker,
    resolve_run_budget_usd,
)

# ---------------------------------------------------------------------------
# resolve_run_budget_usd - precedence
# ---------------------------------------------------------------------------


class TestResolvePrecedence:
    """Layered resolution: env > run_config > seed > default."""

    def test_env_wins_over_run_config_and_seed(self) -> None:
        env = {ENV_MAX_COST_USD: "2.50"}
        result = resolve_run_budget_usd(
            run_config_value=10.0,
            seed_value=5.0,
            env=env,
        )
        assert result == pytest.approx(2.50)

    def test_run_config_used_when_env_unset(self) -> None:
        result = resolve_run_budget_usd(
            run_config_value=7.0,
            seed_value=3.0,
            env={},
        )
        assert result == pytest.approx(7.0)

    def test_seed_used_when_env_and_run_config_missing(self) -> None:
        result = resolve_run_budget_usd(
            run_config_value=None,
            seed_value=4.25,
            env={},
        )
        assert result == pytest.approx(4.25)

    def test_default_zero_when_no_source(self) -> None:
        result = resolve_run_budget_usd(env={})
        assert result == 0.0

    def test_explicit_default_used(self) -> None:
        result = resolve_run_budget_usd(env={}, default=1.5)
        assert result == pytest.approx(1.5)

    def test_run_config_zero_falls_through_to_seed(self) -> None:
        # ``run_config.json`` defaults the field to 0.0 - that should not
        # mask a positive seed value.
        result = resolve_run_budget_usd(
            run_config_value=0.0,
            seed_value=2.0,
            env={},
        )
        assert result == pytest.approx(2.0)

    def test_seed_zero_falls_through_to_default(self) -> None:
        result = resolve_run_budget_usd(
            run_config_value=None,
            seed_value=0.0,
            env={},
            default=0.0,
        )
        assert result == 0.0


class TestResolveEnvParsing:
    """Env-var hardening: typos, whitespace, negatives must not break startup."""

    def test_invalid_env_string_falls_back(self) -> None:
        env = {ENV_MAX_COST_USD: "not-a-number"}
        result = resolve_run_budget_usd(
            run_config_value=3.0,
            env=env,
        )
        assert result == pytest.approx(3.0)

    def test_blank_env_falls_back(self) -> None:
        env = {ENV_MAX_COST_USD: "   "}
        result = resolve_run_budget_usd(
            run_config_value=2.0,
            env=env,
        )
        assert result == pytest.approx(2.0)

    def test_negative_env_clamped_to_zero(self) -> None:
        env = {ENV_MAX_COST_USD: "-1.5"}
        # Negative means "unlimited" - safer than honouring as a -$1.50 cap.
        result = resolve_run_budget_usd(env=env)
        assert result == 0.0

    def test_env_supports_decimals(self) -> None:
        env = {ENV_MAX_COST_USD: "0.0125"}
        result = resolve_run_budget_usd(env=env)
        assert result == pytest.approx(0.0125)


# ---------------------------------------------------------------------------
# Cap-enforcement via CostTracker
# ---------------------------------------------------------------------------


class TestCapEnforcement:
    """End-to-end: resolved cap drives ``CostTracker.can_spawn`` / ``should_stop``."""

    def test_sub_cap_runs_unaffected(self) -> None:
        """At 50% of the cap the tracker still permits new agent spawns."""
        cap = resolve_run_budget_usd(env={ENV_MAX_COST_USD: "1.00"})
        tracker = CostTracker(run_id="kf6-sub-cap", budget_usd=cap)
        tracker.record("agent-A", "task-1", "haiku", 1000, 500, cost_usd=0.50)

        status = tracker.status()
        assert status.percentage_used == pytest.approx(0.50)
        assert status.should_stop is False
        assert tracker.can_spawn() is True

    def test_at_cap_stops_spawning(self) -> None:
        """Reaching 100% of the cap flips ``should_stop`` and blocks spawns."""
        cap = resolve_run_budget_usd(env={ENV_MAX_COST_USD: "1.00"})
        tracker = CostTracker(run_id="kf6-at-cap", budget_usd=cap)
        tracker.record("agent-A", "task-1", "haiku", 1000, 500, cost_usd=1.00)

        status = tracker.status()
        assert status.percentage_used == pytest.approx(1.00)
        assert status.should_stop is True
        assert tracker.can_spawn() is False

    def test_over_cap_stays_blocked(self) -> None:
        """Cumulative spend over the cap remains a hard-stop."""
        cap = resolve_run_budget_usd(env={ENV_MAX_COST_USD: "0.50"})
        tracker = CostTracker(run_id="kf6-over-cap", budget_usd=cap)
        tracker.record("agent-A", "task-1", "sonnet", 1000, 500, cost_usd=0.40)
        assert tracker.can_spawn() is True
        tracker.record("agent-B", "task-2", "sonnet", 1000, 500, cost_usd=0.20)
        assert tracker.can_spawn() is False

    def test_aggregates_across_agents(self) -> None:
        """Cap is per-RUN, not per-agent: spend by multiple agents adds up."""
        cap = resolve_run_budget_usd(env={ENV_MAX_COST_USD: "0.30"})
        tracker = CostTracker(run_id="kf6-multi-agent", budget_usd=cap)
        tracker.record("agent-A", "task-1", "haiku", 100, 50, cost_usd=0.10)
        tracker.record("agent-B", "task-2", "haiku", 100, 50, cost_usd=0.10)
        # Two cheap agents under cap - still allowed.
        assert tracker.can_spawn() is True
        tracker.record("agent-C", "task-3", "haiku", 100, 50, cost_usd=0.15)
        # Third agent pushes the run total to 0.35 >= 0.30 - hard-stop.
        assert tracker.can_spawn() is False

    def test_zero_cap_means_unlimited(self) -> None:
        """When no cap is set the tracker never reports should_stop."""
        cap = resolve_run_budget_usd(env={})
        tracker = CostTracker(run_id="kf6-no-cap", budget_usd=cap)
        tracker.record("agent-A", "task-1", "opus", 100_000, 50_000, cost_usd=99.99)
        status = tracker.status()
        assert status.should_stop is False
        assert tracker.can_spawn() is True


# ---------------------------------------------------------------------------
# CLI propagation
# ---------------------------------------------------------------------------


class TestCliPropagation:
    """``--max-cost-usd N`` round-trips through ``_propagate_env_flags``."""

    def test_flag_sets_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bernstein.cli.run_bootstrap import _propagate_env_flags

        monkeypatch.delenv(ENV_MAX_COST_USD, raising=False)
        _propagate_env_flags(
            profile=False,
            workflow=None,
            routing=None,
            compliance=None,
            sandbox=None,
            container=False,
            container_image=None,
            two_phase_sandbox=False,
            quiet=False,
            task_filter=None,
            auto_pr=False,
            activity_log_path=None,
            audit=False,
            max_cost_usd=2.5,
        )
        import os

        assert os.environ.get(ENV_MAX_COST_USD) == "2.500000"

    def test_no_flag_leaves_env_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bernstein.cli.run_bootstrap import _propagate_env_flags

        monkeypatch.delenv(ENV_MAX_COST_USD, raising=False)
        _propagate_env_flags(
            profile=False,
            workflow=None,
            routing=None,
            compliance=None,
            sandbox=None,
            container=False,
            container_image=None,
            two_phase_sandbox=False,
            quiet=False,
            task_filter=None,
            auto_pr=False,
            activity_log_path=None,
            audit=False,
            max_cost_usd=None,
        )
        import os

        assert ENV_MAX_COST_USD not in os.environ

    def test_zero_flag_is_treated_as_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bernstein.cli.run_bootstrap import _propagate_env_flags

        monkeypatch.delenv(ENV_MAX_COST_USD, raising=False)
        _propagate_env_flags(
            profile=False,
            workflow=None,
            routing=None,
            compliance=None,
            sandbox=None,
            container=False,
            container_image=None,
            two_phase_sandbox=False,
            quiet=False,
            task_filter=None,
            auto_pr=False,
            activity_log_path=None,
            audit=False,
            max_cost_usd=0.0,
        )
        import os

        # Off-by-default: 0.0 (or None) does not propagate so existing flows
        # remain identical to the pre-flag behaviour.
        assert ENV_MAX_COST_USD not in os.environ
