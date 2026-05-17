"""Unit tests for the simulate report types and renderers (issue #1374).

Covers:

* ``to_dict`` round-trip for every dataclass.
* JSON render is sorted and stable across runs.
* Markdown render contains every required section.
* Mermaid graph compiles to a node-id-safe string.
* Empty / minimal report still renders.
"""

from __future__ import annotations

import json

from bernstein.core.simulate.report import (
    AggregateBands,
    Bottleneck,
    CriterionProfileBias,
    DecisionEdge,
    SimulationOptions,
    SimulationReport,
    TaskPrediction,
    render_json,
    render_markdown,
)


def _make_prediction(**overrides: object) -> TaskPrediction:
    base: dict[str, object] = {
        "task_id": "plan-0-0",
        "title": "Sample task",
        "role": "backend",
        "adapter": "mock",
        "cost_p50": 0.10,
        "cost_p90": 0.30,
        "latency_p50": 60.0,
        "latency_p90": 180.0,
        "abandon_probability": 0.05,
        "blast_radius_score": 0.10,
        "depends_on": (),
        "cold_start": True,
        "budget_violation": False,
    }
    base.update(overrides)
    return TaskPrediction(**base)  # type: ignore[arg-type]


def _make_report(tasks: tuple[TaskPrediction, ...] = ()) -> SimulationReport:
    aggregate = AggregateBands(
        total_cost_p50=sum(t.cost_p50 for t in tasks),
        total_cost_p90=sum(t.cost_p90 for t in tasks),
        wall_clock_p50=max((t.latency_p50 for t in tasks), default=0.0),
        wall_clock_p90=max((t.latency_p90 for t in tasks), default=0.0),
        expected_abandonments=sum(t.abandon_probability for t in tasks),
        max_blast_radius=max((t.blast_radius_score for t in tasks), default=0.0),
    )
    bias = CriterionProfileBias(speed=0.25, cost=0.25, quality=0.25, safety=0.25)
    return SimulationReport(
        plan_name="Demo",
        plan_path="/tmp/demo.yaml",
        seed=42,
        task_count=len(tasks),
        tasks=tasks,
        aggregate=aggregate,
        bottlenecks=(),
        decision_edges=tuple(DecisionEdge(from_task="START", to_task=t.task_id, label=t.role) for t in tasks),
        criterion_bias=bias,
        history_samples=0,
        cold_start=True,
        notes=("note one", "note two"),
    )


# ---------------------------------------------------------------------------
# to_dict round-trips
# ---------------------------------------------------------------------------


def test_simulation_options_defaults() -> None:
    opts = SimulationOptions()
    assert opts.seed == 42
    assert opts.from_traces == 50
    assert opts.budget_cap is None


def test_task_prediction_to_dict_roundtrip() -> None:
    pred = _make_prediction()
    raw = pred.to_dict()
    assert raw["task_id"] == "plan-0-0"
    assert raw["cold_start"] is True
    assert isinstance(raw["depends_on"], list)


def test_task_prediction_to_dict_rounds_floats() -> None:
    pred = _make_prediction(cost_p50=0.123456789)
    raw = pred.to_dict()
    assert raw["cost_p50"] == 0.1235


def test_aggregate_bands_to_dict() -> None:
    agg = AggregateBands(
        total_cost_p50=1.0,
        total_cost_p90=2.0,
        wall_clock_p50=10.0,
        wall_clock_p90=20.0,
        expected_abandonments=0.5,
        max_blast_radius=0.7,
        budget_cap=5.0,
        budget_breach=False,
    )
    raw = agg.to_dict()
    assert raw["budget_cap"] == 5.0
    assert raw["budget_breach"] is False


def test_bottleneck_to_dict() -> None:
    bn = Bottleneck(task_id="plan-0-1", title="x", reason="fan_out", score=2.5)
    raw = bn.to_dict()
    assert raw == {"task_id": "plan-0-1", "title": "x", "reason": "fan_out", "score": 2.5}


def test_decision_edge_to_dict() -> None:
    edge = DecisionEdge(from_task="START", to_task="plan-0-0", label="backend")
    raw = edge.to_dict()
    assert raw == {"from": "START", "to": "plan-0-0", "label": "backend"}


def test_criterion_profile_bias_to_dict() -> None:
    bias = CriterionProfileBias(speed=0.1, cost=0.2, quality=0.3, safety=0.4)
    raw = bias.to_dict()
    assert raw["speed"] == 0.1


# ---------------------------------------------------------------------------
# render_json
# ---------------------------------------------------------------------------


def test_render_json_produces_valid_json() -> None:
    report = _make_report((_make_prediction(),))
    out = render_json(report)
    parsed = json.loads(out)
    assert parsed["plan_name"] == "Demo"
    assert isinstance(parsed["tasks"], list)


def test_render_json_is_sorted_for_stability() -> None:
    report = _make_report((_make_prediction(),))
    out_a = render_json(report)
    out_b = render_json(report)
    assert out_a == out_b


def test_render_json_includes_notes() -> None:
    report = _make_report()
    out = render_json(report)
    parsed = json.loads(out)
    assert parsed["notes"] == ["note one", "note two"]


def test_render_json_empty_tasks() -> None:
    report = _make_report()
    out = render_json(report)
    parsed = json.loads(out)
    assert parsed["task_count"] == 0
    assert parsed["tasks"] == []


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_contains_required_sections() -> None:
    report = _make_report((_make_prediction(),))
    md = render_markdown(report)
    assert "# Bernstein simulate" in md
    assert "## TL;DR" in md
    assert "## Per-task predictions" in md
    assert "## Bottlenecks" in md
    assert "## Criterion-profile bias" in md
    assert "## Decision flow" in md


def test_render_markdown_includes_mermaid_block() -> None:
    report = _make_report((_make_prediction(),))
    md = render_markdown(report)
    assert "```mermaid" in md
    assert "graph TD" in md


def test_render_markdown_no_bottleneck_says_none() -> None:
    report = _make_report((_make_prediction(),))
    md = render_markdown(report)
    assert "None identified" in md


def test_render_markdown_lists_bottlenecks() -> None:
    pred = _make_prediction()
    report = _make_report((pred,))
    report = SimulationReport(
        plan_name=report.plan_name,
        plan_path=report.plan_path,
        seed=report.seed,
        task_count=report.task_count,
        tasks=report.tasks,
        aggregate=report.aggregate,
        bottlenecks=(Bottleneck(task_id=pred.task_id, title=pred.title, reason="fan_out", score=3.0),),
        decision_edges=report.decision_edges,
        criterion_bias=report.criterion_bias,
        history_samples=report.history_samples,
        cold_start=report.cold_start,
        notes=report.notes,
    )
    md = render_markdown(report)
    assert "fan_out" in md


def test_render_markdown_shows_budget_cap_when_set() -> None:
    pred = _make_prediction()
    base = _make_report((pred,))
    new_agg = AggregateBands(
        total_cost_p50=base.aggregate.total_cost_p50,
        total_cost_p90=base.aggregate.total_cost_p90,
        wall_clock_p50=base.aggregate.wall_clock_p50,
        wall_clock_p90=base.aggregate.wall_clock_p90,
        expected_abandonments=base.aggregate.expected_abandonments,
        max_blast_radius=base.aggregate.max_blast_radius,
        budget_cap=1.0,
        budget_breach=True,
    )
    report = SimulationReport(
        plan_name=base.plan_name,
        plan_path=base.plan_path,
        seed=base.seed,
        task_count=base.task_count,
        tasks=base.tasks,
        aggregate=new_agg,
        bottlenecks=(),
        decision_edges=base.decision_edges,
        criterion_bias=base.criterion_bias,
        history_samples=0,
        cold_start=False,
        notes=(),
    )
    md = render_markdown(report)
    assert "Budget cap" in md
    assert "BREACH" in md


def test_render_markdown_cold_start_note_present() -> None:
    report = _make_report((_make_prediction(),))
    md = render_markdown(report)
    assert "cold-start" in md


def test_render_markdown_budget_violation_marker_in_table() -> None:
    pred = _make_prediction(budget_violation=True, title="risky")
    report = _make_report((pred,))
    md = render_markdown(report)
    # Marker is appended after the title in the table cell.
    assert " !" in md


def test_render_markdown_handles_zero_tasks() -> None:
    report = _make_report()
    md = render_markdown(report)
    # Render should not blow up; should still have all section headers.
    assert "## Per-task predictions" in md
