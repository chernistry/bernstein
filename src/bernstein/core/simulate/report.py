"""Structured simulation report types and rendering helpers.

The report is the public contract of :func:`bernstein.core.simulate.simulate`.
It is deliberately JSON-friendly (frozen dataclasses with ``to_dict``) so
operators can persist it, diff it across runs, and pipe it into downstream
dashboards without binding to internal types.

Two renderers are provided:

* :func:`render_json` - canonical JSON sidecar.
* :func:`render_markdown` - human-readable summary with decision-tree
  ASCII, criterion-profile bias chart, and bottleneck table.

Both are pure functions: they accept a :class:`SimulationReport` and
return a string. They do not touch the filesystem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AggregateBands",
    "Bottleneck",
    "CriterionProfileBias",
    "DecisionEdge",
    "SimulationOptions",
    "SimulationReport",
    "TaskPrediction",
    "render_json",
    "render_markdown",
]


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SimulationOptions:
    """Knobs controlling a simulation run.

    Attributes:
        seed: Deterministic random seed. Same seed + same plan + same
            traces yields byte-identical reports.
        from_traces: Maximum historical trace records to consult per
            (role, adapter) pair. Newest records win.
        budget_cap: Optional USD ceiling. When set, the simulator marks
            tasks whose predicted p90 cost would push the running aggregate
            over the cap. ``None`` means no cap.
        metrics_dir: Path to ``.sdd/metrics`` for cost history. ``None``
            uses cold-start heuristics throughout.
        traces_dir: Path to ``.sdd/traces`` for abandonment/latency
            calibration. ``None`` uses uniform priors.
    """

    seed: int = 42
    from_traces: int = 50
    budget_cap: float | None = None
    metrics_dir: str | None = None
    traces_dir: str | None = None


# ---------------------------------------------------------------------------
# Per-task forecast
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskPrediction:
    """Forecast for a single task in the plan.

    Attributes:
        task_id: Stable id from the plan loader (e.g. ``"plan-0-1"``).
        title: Task title verbatim from the plan.
        role: Agent role (``"backend"``, ``"qa"`` ...).
        adapter: Adapter id used in the forecast (defaults to ``"mock"``).
        cost_p50: Median predicted USD spend for this task.
        cost_p90: 90th-percentile predicted USD spend for this task.
        latency_p50: Median predicted wall-clock seconds.
        latency_p90: 90th-percentile predicted wall-clock seconds.
        abandon_probability: Probability in [0, 1] that the task is
            abandoned (manager rejects or worker bails out).
        blast_radius_score: Estimated blast-radius score in [0, 1].
        depends_on: Task ids this task waits on (verbatim from plan).
        cold_start: True when no historical samples backed the cost band
            and the heuristic fallback was used.
        budget_violation: True when the operator-supplied ``budget_cap``
            is exceeded by this task's p90 contribution.
    """

    task_id: str
    title: str
    role: str
    adapter: str
    cost_p50: float
    cost_p90: float
    latency_p50: float
    latency_p90: float
    abandon_probability: float
    blast_radius_score: float
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    cold_start: bool = False
    budget_violation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "role": self.role,
            "adapter": self.adapter,
            "cost_p50": round(self.cost_p50, 4),
            "cost_p90": round(self.cost_p90, 4),
            "latency_p50": round(self.latency_p50, 2),
            "latency_p90": round(self.latency_p90, 2),
            "abandon_probability": round(self.abandon_probability, 4),
            "blast_radius_score": round(self.blast_radius_score, 4),
            "depends_on": list(self.depends_on),
            "cold_start": self.cold_start,
            "budget_violation": self.budget_violation,
        }


# ---------------------------------------------------------------------------
# Aggregate bands
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AggregateBands:
    """Roll-up bands across all tasks in the plan.

    Attributes:
        total_cost_p50: Sum of per-task ``cost_p50`` (USD).
        total_cost_p90: Sum of per-task ``cost_p90`` (USD).
        wall_clock_p50: Critical-path latency at p50 (seconds).
        wall_clock_p90: Critical-path latency at p90 (seconds).
        expected_abandonments: Sum of per-task ``abandon_probability``.
        max_blast_radius: Highest per-task blast-radius score.
        budget_cap: Operator-supplied USD ceiling (echoed). ``None`` when
            unset.
        budget_breach: True when ``total_cost_p90 > budget_cap`` and a cap
            was supplied.
    """

    total_cost_p50: float
    total_cost_p90: float
    wall_clock_p50: float
    wall_clock_p90: float
    expected_abandonments: float
    max_blast_radius: float
    budget_cap: float | None = None
    budget_breach: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost_p50": round(self.total_cost_p50, 4),
            "total_cost_p90": round(self.total_cost_p90, 4),
            "wall_clock_p50": round(self.wall_clock_p50, 2),
            "wall_clock_p90": round(self.wall_clock_p90, 2),
            "expected_abandonments": round(self.expected_abandonments, 4),
            "max_blast_radius": round(self.max_blast_radius, 4),
            "budget_cap": self.budget_cap,
            "budget_breach": self.budget_breach,
        }


# ---------------------------------------------------------------------------
# Bottlenecks and decision edges
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bottleneck:
    """A task flagged as a bottleneck on the simulated critical path.

    Attributes:
        task_id: Task id of the bottleneck.
        title: Human-readable task title.
        reason: Short label (``"fan_out"``, ``"high_abandon"``,
            ``"long_latency"``, ``"high_blast_radius"``).
        score: Magnitude that justified the flag (larger == worse).
    """

    task_id: str
    title: str
    reason: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "reason": self.reason,
            "score": round(self.score, 4),
        }


@dataclass(frozen=True, slots=True)
class DecisionEdge:
    """One directed edge in the simulated decision flow.

    Attributes:
        from_task: Predecessor task id (``"START"`` for plan entry).
        to_task: Successor task id.
        label: Short edge label (typically the predecessor's role).
    """

    from_task: str
    to_task: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_task, "to": self.to_task, "label": self.label}


@dataclass(frozen=True, slots=True)
class CriterionProfileBias:
    """Aggregate bias of the predicted decision flow per criterion.

    Each task contributes its role weight to one of the four canonical
    criterion buckets (``speed``, ``cost``, ``quality``, ``safety``).
    Operators read this to spot lop-sided plans (e.g. 80% of work on
    "speed" with no ``security`` review).

    Attributes:
        speed: Share in [0, 1] of work biased toward speed.
        cost: Share in [0, 1] biased toward cost minimisation.
        quality: Share in [0, 1] biased toward quality / verification.
        safety: Share in [0, 1] biased toward safety / security review.
    """

    speed: float
    cost: float
    quality: float
    safety: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "speed": round(self.speed, 4),
            "cost": round(self.cost, 4),
            "quality": round(self.quality, 4),
            "safety": round(self.safety, 4),
        }


# ---------------------------------------------------------------------------
# Top-level report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SimulationReport:
    """Structured output of a simulation run.

    The report is JSON-stable across runs of the same plan with the same
    seed and the same trace history. Operators diff it across plan edits
    to spot regressions before paying real tokens.
    """

    plan_name: str
    plan_path: str
    seed: int
    task_count: int
    tasks: tuple[TaskPrediction, ...]
    aggregate: AggregateBands
    bottlenecks: tuple[Bottleneck, ...]
    decision_edges: tuple[DecisionEdge, ...]
    criterion_bias: CriterionProfileBias
    history_samples: int
    cold_start: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_name": self.plan_name,
            "plan_path": self.plan_path,
            "seed": self.seed,
            "task_count": self.task_count,
            "tasks": [t.to_dict() for t in self.tasks],
            "aggregate": self.aggregate.to_dict(),
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "decision_edges": [e.to_dict() for e in self.decision_edges],
            "criterion_bias": self.criterion_bias.to_dict(),
            "history_samples": self.history_samples,
            "cold_start": self.cold_start,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_json(report: SimulationReport, *, indent: int = 2) -> str:
    """Return a canonical JSON string for ``report``.

    Sort keys for stability across runs.
    """
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True)


def _bar(value: float, width: int = 20) -> str:
    """Render a bracketed unit-bar of ``width`` characters for ``value`` in [0, 1]."""
    clamped = max(0.0, min(1.0, value))
    filled = round(clamped * width)
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_usd(value: float) -> str:
    return f"${value:.2f}"


def render_markdown(report: SimulationReport) -> str:
    """Return a human-readable Markdown summary.

    Includes:

    * TL;DR with cost band, wall-clock, abandonment, max blast-radius.
    * Per-task table with cost/abandon/blast-radius columns.
    * Bottleneck section.
    * Criterion-profile bias chart (ASCII bars).
    * Decision-flow as a Mermaid graph (operators can paste into docs).
    """
    agg = report.aggregate
    bias = report.criterion_bias

    lines: list[str] = []
    lines.extend(
        (
            f"# Bernstein simulate - {report.plan_name}",
            "",
            f"Plan: `{report.plan_path}` - seed `{report.seed}` - {report.task_count} task(s)",
            "",
            "## TL;DR",
            "",
            f"- **Cost band**: {_format_usd(agg.total_cost_p50)} (p50) .. {_format_usd(agg.total_cost_p90)} (p90)",
            f"- **Wall-clock**: {agg.wall_clock_p50:.0f}s (p50) .. {agg.wall_clock_p90:.0f}s (p90)",
            f"- **Expected abandonments**: {agg.expected_abandonments:.2f}",
            f"- **Max blast-radius**: {agg.max_blast_radius:.2f}",
        )
    )
    if agg.budget_cap is not None:
        flag = " - BREACH" if agg.budget_breach else " - within cap"
        lines.append(f"- **Budget cap**: {_format_usd(agg.budget_cap)}{flag}")
    if report.cold_start:
        lines.append("- *Note: cold-start, heuristic fallback used.*")
    lines.extend(
        (
            "",
            "## Per-task predictions",
            "",
            "| Task | Role | Cost p50 | Cost p90 | Abandon | Blast |",
            "|------|------|---------:|---------:|--------:|------:|",
        )
    )
    for task in report.tasks:
        warn = " !" if task.budget_violation else ""
        lines.append(
            f"| {task.task_id} {task.title}{warn} | {task.role} | "
            f"{_format_usd(task.cost_p50)} | {_format_usd(task.cost_p90)} | "
            f"{task.abandon_probability:.2f} | {task.blast_radius_score:.2f} |"
        )
    lines.extend(("", "## Bottlenecks", ""))
    if report.bottlenecks:
        for bn in report.bottlenecks:
            lines.append(f"- `{bn.task_id}` ({bn.reason}, score={bn.score:.2f}) - {bn.title}")
    else:
        lines.append("- None identified.")
    lines.extend(
        (
            "",
            "## Criterion-profile bias",
            "",
            f"- speed    {_bar(bias.speed)}  {bias.speed:.2f}",
            f"- cost     {_bar(bias.cost)}  {bias.cost:.2f}",
            f"- quality  {_bar(bias.quality)}  {bias.quality:.2f}",
            f"- safety   {_bar(bias.safety)}  {bias.safety:.2f}",
            "",
            "## Decision flow",
            "",
            "```mermaid",
            "graph TD",
        )
    )
    for edge in report.decision_edges:
        label = f"|{edge.label}|" if edge.label else ""
        # Mermaid node ids must be ascii-safe; the plan loader already
        # produces ``plan-{stage}-{step}`` ids that satisfy this.
        from_node = edge.from_task.replace("-", "_")
        to_node = edge.to_task.replace("-", "_")
        lines.append(f"  {from_node} -->{label} {to_node}")
    lines.extend(("```", ""))

    if report.notes:
        lines.extend(("## Notes", ""))
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)
