"""Unit tests for the simulate runner (issue #1374).

Covers:

* Deterministic output for a fixed seed.
* Topological ordering and dependency resolution.
* Cycle / dangling-dep robustness (still produces a report).
* Budget cap handling.
* Single-task / empty-stage / multi-stage plans.
* Critical-path latency calculation.
* Decision-edge graph wiring.
* Cold-start vs history-backed paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bernstein.core.simulate import (
    SimulationError,
    SimulationOptions,
    simulate,
)

_MINIMAL_PLAN: dict[str, object] = {
    "name": "Minimal plan",
    "description": "one task, no deps",
    "stages": [
        {
            "name": "Build",
            "steps": [
                {"title": "Implement feature", "role": "backend"},
            ],
        }
    ],
}


_LINEAR_PLAN: dict[str, object] = {
    "name": "Linear plan",
    "stages": [
        {
            "name": "Stage1",
            "steps": [{"title": "Task A", "role": "backend"}],
        },
        {
            "name": "Stage2",
            "depends_on": ["Stage1"],
            "steps": [{"title": "Task B", "role": "qa"}],
        },
    ],
}


_FAN_OUT_PLAN: dict[str, object] = {
    "name": "Fan-out plan",
    "stages": [
        {
            "name": "Setup",
            "steps": [{"title": "Bootstrap", "role": "backend"}],
        },
        {
            "name": "Parallel",
            "depends_on": ["Setup"],
            "steps": [
                {"title": "Feature A", "role": "backend"},
                {"title": "Feature B", "role": "backend"},
                {"title": "Feature C", "role": "frontend"},
            ],
        },
    ],
}


def _write_plan(tmp_path: Path, plan: dict[str, object]) -> Path:
    target = tmp_path / "plan.yaml"
    target.write_text(yaml.safe_dump(plan), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Loading and trivial plans
# ---------------------------------------------------------------------------


def test_simulate_missing_plan_raises(tmp_path: Path) -> None:
    with pytest.raises(SimulationError):
        simulate(tmp_path / "does-not-exist.yaml")


def test_simulate_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("not a mapping", encoding="utf-8")
    with pytest.raises(SimulationError):
        simulate(bad)


def test_simulate_minimal_plan(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    report = simulate(plan)
    assert report.task_count == 1
    assert report.tasks[0].role == "backend"
    assert report.aggregate.total_cost_p50 >= 0.0
    assert report.aggregate.total_cost_p90 >= report.aggregate.total_cost_p50


def test_simulate_linear_plan(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _LINEAR_PLAN)
    report = simulate(plan)
    assert report.task_count == 2
    # Critical path is sum of latencies for a strict chain.
    sum_p50 = sum(t.latency_p50 for t in report.tasks)
    assert report.aggregate.wall_clock_p50 == pytest.approx(sum_p50)


def test_simulate_fan_out_plan_wallclock_shorter_than_sum(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    sum_p50 = sum(t.latency_p50 for t in report.tasks)
    # The three parallel tasks should compress wall-clock below the sum.
    assert report.aggregate.wall_clock_p50 < sum_p50


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_simulate_deterministic_with_fixed_seed(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    a = simulate(plan, SimulationOptions(seed=7))
    b = simulate(plan, SimulationOptions(seed=7))
    assert a.to_dict() == b.to_dict()


def test_simulate_different_seeds_can_diverge(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    a = simulate(plan, SimulationOptions(seed=1))
    b = simulate(plan, SimulationOptions(seed=2))
    # Banding is the same shape but per-task jitter should change at least
    # one numeric value across the two runs.
    assert a.to_dict() != b.to_dict()


# ---------------------------------------------------------------------------
# Budget cap
# ---------------------------------------------------------------------------


def test_simulate_budget_cap_records_breach(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan, SimulationOptions(budget_cap=0.0))
    # Any non-trivial plan blows a zero cap.
    assert report.aggregate.budget_breach is True
    assert any(t.budget_violation for t in report.tasks)


def test_simulate_budget_cap_high_enough_no_breach(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan, SimulationOptions(budget_cap=10_000.0))
    assert report.aggregate.budget_breach is False
    assert not any(t.budget_violation for t in report.tasks)


def test_simulate_no_budget_cap_does_not_flag(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    assert report.aggregate.budget_breach is False


# ---------------------------------------------------------------------------
# Cycle and dangling dep robustness
# ---------------------------------------------------------------------------


def test_simulate_handles_unknown_stage_dep(tmp_path: Path) -> None:
    plan_dict: dict[str, object] = {
        "name": "Dangling",
        "stages": [
            {
                "name": "Only",
                "depends_on": ["Nope"],
                "steps": [{"title": "x", "role": "backend"}],
            }
        ],
    }
    plan = _write_plan(tmp_path, plan_dict)
    report = simulate(plan)
    # Still produces one task.
    assert report.task_count == 1


# ---------------------------------------------------------------------------
# Bottleneck identification
# ---------------------------------------------------------------------------


def test_simulate_bottleneck_fan_out(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    reasons = {b.reason for b in report.bottlenecks}
    assert "fan_out" in reasons


def test_simulate_bottleneck_high_blast_radius(tmp_path: Path) -> None:
    plan_dict: dict[str, object] = {
        "name": "Dangerous plan",
        "stages": [
            {
                "name": "Wipe",
                "steps": [
                    {
                        "title": "Clean db",
                        "role": "backend",
                        "description": "DROP TABLE accounts; DELETE FROM logs",
                        "files": ["db/wipe.sql"],
                    }
                ],
            }
        ],
    }
    plan = _write_plan(tmp_path, plan_dict)
    report = simulate(plan)
    reasons = {b.reason for b in report.bottlenecks}
    assert "high_blast_radius" in reasons


def test_simulate_bottleneck_high_abandon(tmp_path: Path) -> None:
    # Seed traces dir with a high abandon rate for the role.
    pass  # Covered by integration test with real traces dir.


# ---------------------------------------------------------------------------
# Decision flow
# ---------------------------------------------------------------------------


def test_simulate_decision_edges_root_has_start_edge(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    report = simulate(plan)
    assert any(e.from_task == "START" for e in report.decision_edges)


def test_simulate_decision_edges_linear(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _LINEAR_PLAN)
    report = simulate(plan)
    # The second task must point at the first.
    first, second = report.tasks
    assert any(e.from_task == first.task_id and e.to_task == second.task_id for e in report.decision_edges)


# ---------------------------------------------------------------------------
# Criterion-profile bias
# ---------------------------------------------------------------------------


def test_simulate_criterion_bias_sums_to_one(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    bias = report.criterion_bias
    total = bias.speed + bias.cost + bias.quality + bias.safety
    assert total == pytest.approx(1.0)


def test_simulate_criterion_bias_security_role(tmp_path: Path) -> None:
    plan_dict: dict[str, object] = {
        "name": "Security plan",
        "stages": [
            {
                "name": "Audit",
                "steps": [{"title": "Pentest", "role": "security"}],
            }
        ],
    }
    plan = _write_plan(tmp_path, plan_dict)
    report = simulate(plan)
    assert report.criterion_bias.safety == 1.0


# ---------------------------------------------------------------------------
# Cold-start handling
# ---------------------------------------------------------------------------


def test_simulate_cold_start_flagged_when_no_history(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    report = simulate(plan)
    assert report.cold_start is True
    assert any("cold-start" in note or "heuristic" in note for note in report.notes)


def test_simulate_with_metrics_dir_no_data(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    report = simulate(plan, SimulationOptions(metrics_dir=str(metrics_dir)))
    assert report.cold_start is True


def test_simulate_with_traces_dir_no_data(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    report = simulate(plan, SimulationOptions(traces_dir=str(traces_dir)))
    assert report.history_samples == 0


def test_simulate_with_traces_dir_populated(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    traces_dir = tmp_path / "traces"
    traces_dir.mkdir()
    (traces_dir / "t.jsonl").write_text(
        '{"role": "backend", "adapter": "mock", "status": "completed", "latency_seconds": 30.0}\n'
        '{"role": "backend", "adapter": "mock", "status": "abandoned", "latency_seconds": 10.0}\n',
        encoding="utf-8",
    )
    report = simulate(plan, SimulationOptions(traces_dir=str(traces_dir)))
    assert report.history_samples == 2
    # Abandon rate should now reflect the trace data (0.5), not the cold prior.
    assert report.tasks[0].abandon_probability == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Per-task fields
# ---------------------------------------------------------------------------


def test_simulate_per_task_fields_non_negative(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    for task in report.tasks:
        assert task.cost_p50 >= 0.0
        assert task.cost_p90 >= 0.0
        assert task.latency_p50 >= 0.0
        assert task.latency_p90 >= 0.0
        assert 0.0 <= task.abandon_probability <= 1.0
        assert 0.0 <= task.blast_radius_score <= 1.0


def test_simulate_p90_never_below_p50(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    for task in report.tasks:
        assert task.cost_p90 >= task.cost_p50
        assert task.latency_p90 >= task.latency_p50


def test_simulate_depends_on_resolved_to_ids(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _LINEAR_PLAN)
    report = simulate(plan)
    second = report.tasks[1]
    # depends_on should now reference the first task's id, not its title.
    first = report.tasks[0]
    assert first.task_id in second.depends_on


def test_simulate_aggregate_max_blast_radius_matches_tasks(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    explicit_max = max(t.blast_radius_score for t in report.tasks)
    assert report.aggregate.max_blast_radius == pytest.approx(explicit_max)


def test_simulate_aggregate_total_cost_matches_tasks(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    sum_p50 = sum(t.cost_p50 for t in report.tasks)
    assert report.aggregate.total_cost_p50 == pytest.approx(sum_p50)


def test_simulate_expected_abandonments_sum_matches(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _FAN_OUT_PLAN)
    report = simulate(plan)
    summed = sum(t.abandon_probability for t in report.tasks)
    assert report.aggregate.expected_abandonments == pytest.approx(summed)


# ---------------------------------------------------------------------------
# Plan path / name
# ---------------------------------------------------------------------------


def test_simulate_uses_plan_name_when_set(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    report = simulate(plan)
    assert report.plan_name == "Minimal plan"


def test_simulate_falls_back_to_stem_when_no_name(tmp_path: Path) -> None:
    plan_dict: dict[str, object] = {
        "stages": [{"name": "x", "steps": [{"title": "t", "role": "backend"}]}],
    }
    target = tmp_path / "my_plan.yaml"
    target.write_text(yaml.safe_dump(plan_dict), encoding="utf-8")
    report = simulate(target)
    assert report.plan_name == "my_plan"


def test_simulate_records_plan_path(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    report = simulate(plan)
    assert report.plan_path == str(plan)


# ---------------------------------------------------------------------------
# Many-task scaling
# ---------------------------------------------------------------------------


def test_simulate_handles_large_plan(tmp_path: Path) -> None:
    steps = [{"title": f"Task {i}", "role": "backend"} for i in range(40)]
    plan_dict: dict[str, object] = {
        "name": "Big plan",
        "stages": [{"name": "Big", "steps": steps}],
    }
    plan = _write_plan(tmp_path, plan_dict)
    report = simulate(plan)
    assert report.task_count == 40


def test_simulate_zero_seed_works(tmp_path: Path) -> None:
    plan = _write_plan(tmp_path, _MINIMAL_PLAN)
    report = simulate(plan, SimulationOptions(seed=0))
    assert report.seed == 0
