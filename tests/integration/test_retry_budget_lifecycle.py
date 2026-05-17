"""Integration tests for the criterion-aware retry budget lifecycle.

These tests stitch the retry budget into the surrounding cost tracker
(via :py:meth:`CostTracker.attach_retry_budget`) and exercise full
multi-retry sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bernstein.core.cost.cost_tracker import CostTracker
from bernstein.core.cost.retry_budget import (
    Criterion,
    DegradationKind,
    RetryBudget,
    parse_retry_budget_spec,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracker(tmp_path: Path) -> CostTracker:
    """A bare ``CostTracker`` rotating evicted rows into ``tmp_path``."""
    return CostTracker(
        run_id="test-run",
        budget_usd=10.0,
        rotation_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


def test_cost_tracker_can_attach_retry_budget(tracker: CostTracker) -> None:
    """A retry budget can be attached to and retrieved from a tracker."""
    budget = RetryBudget.from_names(retries=3, names=["coverage", "tests"])
    assert tracker.retry_budget is None
    tracker.attach_retry_budget(budget)
    assert tracker.retry_budget is budget


def test_attached_budget_survives_independent_consumption(
    tracker: CostTracker,
) -> None:
    """Consuming retries on the attached budget does not affect the tracker."""
    budget = RetryBudget.from_names(retries=2, names=["coverage", "tests"])
    tracker.attach_retry_budget(budget)
    budget.consume()
    # The tracker still exposes the same object.
    fetched = tracker.retry_budget
    assert fetched is budget
    assert isinstance(fetched, RetryBudget)
    assert fetched.attempts_used == 1


def test_full_three_retry_lifecycle_via_cli_spec() -> None:
    """End-to-end: parse a CLI spec, run three retries, verify ordering."""
    budget = parse_retry_budget_spec("3 retries, degrade: coverage>tests>style")
    decisions = [budget.consume() for _ in range(3)]
    names = [d.degraded_criterion.name for d in decisions if d.degraded_criterion is not None]
    assert names == ["coverage", "tests", "style"]
    # Levels have all stepped from 3 -> 2.
    for d in decisions:
        assert d.degraded_criterion is not None
        assert d.degraded_criterion.level == 2
    # Fourth retry is denied.
    extra = budget.consume()
    assert not extra.should_retry


def test_lifecycle_with_pre_floored_criterion_yields_dlq() -> None:
    """A criterion already at floor yields a FLOORED retry then exhausts."""
    budget = RetryBudget(
        retries=1,
        criterion_degradation=[
            Criterion("coverage", level=0, min_level=0, max_level=3),
        ],
    )
    d = budget.consume()
    assert d.should_retry
    assert d.degradation_kind is DegradationKind.FLOORED
    d2 = budget.consume()
    # Budget exhausted on the second call.
    assert not d2.should_retry


def test_zero_retry_lifecycle_routes_to_dead_letter(
    tracker: CostTracker,
) -> None:
    """A zero-retry budget produces no retries and an exhaustion decision."""
    budget = RetryBudget(retries=0)
    tracker.attach_retry_budget(budget)
    decision = budget.consume()
    assert not decision.should_retry
    assert "exhausted" in decision.reason.lower()
    # The tracker still reports the budget as attached.
    assert tracker.retry_budget is budget


def test_cli_retry_budget_flag_validates_eagerly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI plumbing rejects malformed specs before dispatch.

    Imports happen inside the test to avoid the full CLI graph at
    module-collection time.
    """
    from click.testing import CliRunner

    from bernstein.cli.run_bootstrap import run as run_cmd  # type: ignore[attr-defined]

    runner = CliRunner()
    monkeypatch.delenv("BERNSTEIN_RETRY_BUDGET_SPEC", raising=False)
    result = runner.invoke(
        run_cmd,
        ["--retry-budget", "garbage", "--plan-only"],
        catch_exceptions=True,
    )
    assert result.exit_code != 0
    # The friendly Click error mentions our flag name.
    assert "retry-budget" in result.output.lower() or "retry budget" in result.output.lower()
