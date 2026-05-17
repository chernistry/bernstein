"""Hypothesis property tests for ``bernstein simulate`` (issue #1374).

Invariants under test:

* Determinism: same plan + same seed -> byte-identical report.
* Monotonicity in budget cap: a stricter cap never widens the no-breach
  region; a relaxed cap never shrinks it.
* No negative cost / latency predictions for any task.
* Cost p90 >= cost p50 for every task.
* Latency p90 >= latency p50 for every task.
* Abandon probability always in [0, 1].
* Blast-radius score always in [0, 1].
* Criterion-profile bias sums to ~1.0 for non-empty plans.
* Aggregate totals are the sum of per-task contributions.
* All decision edges reference tasks that exist (no dangling ids).
* Trace history makes the abandon prediction match the observed rate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.simulate import SimulationOptions, simulate

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_KNOWN_ROLES: list[str] = [
    "backend",
    "frontend",
    "qa",
    "security",
    "docs",
    "devops",
    "architect",
]


_step_strategy = st.fixed_dictionaries(
    {
        "title": st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=15),
        "role": st.sampled_from(_KNOWN_ROLES),
    }
)


_stage_strategy = st.fixed_dictionaries(
    {
        "name": st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=10),
        "steps": st.lists(_step_strategy, min_size=1, max_size=4, unique_by=lambda s: s["title"]),
    }
)


_plan_strategy = st.fixed_dictionaries(
    {
        "name": st.text(min_size=1, max_size=20),
        "stages": st.lists(_stage_strategy, min_size=1, max_size=3, unique_by=lambda s: s["name"]),
    }
)


_SETTINGS = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)


def _write_plan(tmp_path: Path, plan: dict[str, Any]) -> Path:
    target = tmp_path / "plan.yaml"
    target.write_text(yaml.safe_dump(plan), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@given(plan=_plan_strategy, seed=st.integers(min_value=0, max_value=2**16 - 1))
@_SETTINGS
def test_determinism_same_seed_same_report(
    tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any], seed: int
) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    a = simulate(plan_path, SimulationOptions(seed=seed))
    b = simulate(plan_path, SimulationOptions(seed=seed))
    assert a.to_dict() == b.to_dict()


# ---------------------------------------------------------------------------
# Non-negativity
# ---------------------------------------------------------------------------


@given(plan=_plan_strategy)
@_SETTINGS
def test_costs_are_non_negative(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    for task in report.tasks:
        assert task.cost_p50 >= 0.0
        assert task.cost_p90 >= 0.0
        assert task.latency_p50 >= 0.0
        assert task.latency_p90 >= 0.0


@given(plan=_plan_strategy)
@_SETTINGS
def test_p90_at_least_p50(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    for task in report.tasks:
        assert task.cost_p90 >= task.cost_p50
        assert task.latency_p90 >= task.latency_p50


@given(plan=_plan_strategy)
@_SETTINGS
def test_abandon_probability_in_unit_range(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    for task in report.tasks:
        assert 0.0 <= task.abandon_probability <= 1.0


@given(plan=_plan_strategy)
@_SETTINGS
def test_blast_radius_in_unit_range(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    for task in report.tasks:
        assert 0.0 <= task.blast_radius_score <= 1.0


# ---------------------------------------------------------------------------
# Aggregate invariants
# ---------------------------------------------------------------------------


@given(plan=_plan_strategy)
@_SETTINGS
def test_aggregate_cost_matches_sum(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    expected_p50 = sum(t.cost_p50 for t in report.tasks)
    expected_p90 = sum(t.cost_p90 for t in report.tasks)
    assert report.aggregate.total_cost_p50 == pytest.approx(expected_p50)
    assert report.aggregate.total_cost_p90 == pytest.approx(expected_p90)


@given(plan=_plan_strategy)
@_SETTINGS
def test_aggregate_max_blast_radius_correct(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    expected = max((t.blast_radius_score for t in report.tasks), default=0.0)
    assert report.aggregate.max_blast_radius == pytest.approx(expected)


@given(plan=_plan_strategy)
@_SETTINGS
def test_criterion_bias_sums_to_one(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    bias = report.criterion_bias
    total = bias.speed + bias.cost + bias.quality + bias.safety
    if report.task_count > 0:
        assert total == pytest.approx(1.0)
    else:
        assert total == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Decision edges
# ---------------------------------------------------------------------------


@given(plan=_plan_strategy)
@_SETTINGS
def test_decision_edges_reference_real_tasks(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    ids = {t.task_id for t in report.tasks}
    for edge in report.decision_edges:
        assert edge.from_task == "START" or edge.from_task in ids
        assert edge.to_task in ids


# ---------------------------------------------------------------------------
# Budget cap monotonicity
# ---------------------------------------------------------------------------


@given(plan=_plan_strategy, base=st.floats(min_value=0.0, max_value=100.0))
@_SETTINGS
def test_budget_cap_monotonicity(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any], base: float) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    strict = simulate(plan_path, SimulationOptions(budget_cap=base))
    relaxed = simulate(plan_path, SimulationOptions(budget_cap=base + 1000.0))
    # A relaxed cap can never be a breach when the strict cap was within
    # range; and the strict cap can only flag MORE per-task breaches.
    if not strict.aggregate.budget_breach:
        assert not relaxed.aggregate.budget_breach
    strict_violations = sum(1 for t in strict.tasks if t.budget_violation)
    relaxed_violations = sum(1 for t in relaxed.tasks if t.budget_violation)
    assert strict_violations >= relaxed_violations


# ---------------------------------------------------------------------------
# History overrides cold prior
# ---------------------------------------------------------------------------


@given(plan=_plan_strategy)
@_SETTINGS
def test_history_samples_is_non_negative(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    assert report.history_samples >= 0


@given(plan=_plan_strategy)
@_SETTINGS
def test_task_count_matches_tasks_length(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    assert report.task_count == len(report.tasks)


@given(plan=_plan_strategy)
@_SETTINGS
def test_wall_clock_bands_ordered(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    assert report.aggregate.wall_clock_p90 >= report.aggregate.wall_clock_p50


@given(plan=_plan_strategy)
@_SETTINGS
def test_aggregate_p90_at_least_p50(tmp_path_factory: pytest.TempPathFactory, plan: dict[str, Any]) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan_path = _write_plan(tmp_path, plan)
    report = simulate(plan_path)
    assert report.aggregate.total_cost_p90 >= report.aggregate.total_cost_p50


@given(observed_rate=st.floats(min_value=0.0, max_value=1.0))
@_SETTINGS
def test_traces_drive_abandon_prediction(
    tmp_path_factory: pytest.TempPathFactory,
    observed_rate: float,
) -> None:
    tmp_path = tmp_path_factory.mktemp("sim")
    plan = {
        "name": "Single",
        "stages": [
            {
                "name": "Only",
                "steps": [{"title": "Task", "role": "backend"}],
            }
        ],
    }
    plan_path = _write_plan(tmp_path, plan)

    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    # Build a 100-record trace file with the requested abandon rate.
    rate_int = round(observed_rate * 100)
    lines: list[str] = []
    for i in range(100):
        status = "abandoned" if i < rate_int else "completed"
        lines.append('{"role": "backend", "adapter": "mock", "status": "' + status + '", "latency_seconds": 10.0}')
    (traces_dir / "t.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = simulate(plan_path, SimulationOptions(traces_dir=str(traces_dir)))
    assert report.tasks[0].abandon_probability == pytest.approx(rate_int / 100.0, abs=0.01)
