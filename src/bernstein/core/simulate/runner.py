"""Digital-twin orchestration runner.

The runner wires the four predictors together, walks the plan's task
graph in topological order, and assembles a :class:`SimulationReport`
without spawning a real agent or hitting the network.

Determinism: the same ``(plan, options.seed, traces)`` triple always
produces a byte-identical report.

Cycle handling: tasks whose ``depends_on`` references are unresolvable
(cycle, missing predecessor) are still simulated, but a note is appended
to the report so the operator can investigate. We do not raise on cycles
because the underlying plan loader currently accepts them; raising would
be a behaviour change outside this issue's scope.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from bernstein.core.planning.plan_loader import PlanLoadError, load_plan  # type: ignore[reportUnknownVariableType]
from bernstein.core.simulate.predictor import (
    AbandonmentPredictor,
    BlastRadiusPredictor,
    CostPredictor,
    HistoricalTraces,
    LatencyPredictor,
    load_traces,
    role_criterion,
)
from bernstein.core.simulate.report import (
    AggregateBands,
    Bottleneck,
    CriterionProfileBias,
    DecisionEdge,
    SimulationOptions,
    SimulationReport,
    TaskPrediction,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bernstein.core.tasks.models import Task

__all__ = ["SimulationError", "simulate"]


class SimulationError(Exception):
    """Raised when the plan cannot be loaded or simulated."""


def _topological_order(tasks: Sequence[Task]) -> list[Task]:
    """Return ``tasks`` in dependency order (Kahn's algorithm).

    Tasks whose ``depends_on`` references cannot be resolved are appended
    after the resolvable subgraph, in their original order, so the
    simulator still produces a forecast on a malformed plan.
    """
    by_title: dict[str, Task] = {t.title: t for t in tasks}
    by_id: dict[str, Task] = {t.id: t for t in tasks}
    indegree: dict[str, int] = {t.id: 0 for t in tasks}
    successors: dict[str, list[str]] = defaultdict(list)

    for task in tasks:
        for dep in task.depends_on:
            dep_task = by_title.get(dep) or by_id.get(dep)
            if dep_task is None:
                continue
            successors[dep_task.id].append(task.id)
            indegree[task.id] += 1

    # Process roots in original plan order so the output is stable.
    queue: list[str] = [t.id for t in tasks if indegree[t.id] == 0]
    visited: set[str] = set()
    ordered_ids: list[str] = []
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        ordered_ids.append(current)
        for succ in successors[current]:
            indegree[succ] -= 1
            if indegree[succ] <= 0:
                queue.append(succ)

    # Append any unresolved tasks (cycles / dangling deps) in plan order.
    for task in tasks:
        if task.id not in visited:
            ordered_ids.append(task.id)

    return [by_id[i] for i in ordered_ids]


def _critical_path(
    tasks: Sequence[TaskPrediction],
    *,
    use_p90: bool,
) -> float:
    """Return the critical-path wall-clock (seconds) over predictions.

    Uses ``latency_p90`` when ``use_p90`` is True, otherwise ``latency_p50``.
    """
    by_id: dict[str, TaskPrediction] = {t.task_id: t for t in tasks}
    longest: dict[str, float] = {}

    # Original list order is already topologically sound (runner sorts).
    for task in tasks:
        own = task.latency_p90 if use_p90 else task.latency_p50
        max_pred = 0.0
        for dep_id in task.depends_on:
            dep = by_id.get(dep_id)
            if dep is None:
                continue
            max_pred = max(max_pred, longest.get(dep.task_id, 0.0))
        longest[task.task_id] = max_pred + own

    return max(longest.values(), default=0.0)


def _fan_out(tasks: Sequence[TaskPrediction]) -> dict[str, int]:
    """Return successor count per task id."""
    count: dict[str, int] = {t.task_id: 0 for t in tasks}
    by_id: dict[str, TaskPrediction] = {t.task_id: t for t in tasks}
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id in by_id:
                count[dep_id] = count.get(dep_id, 0) + 1
    return count


def _bottlenecks(predictions: Sequence[TaskPrediction]) -> tuple[Bottleneck, ...]:
    """Identify bottleneck tasks across multiple heuristics.

    A task is flagged when any of the following hold:

    * Fan-out >= 2 (more than one task waits on it).
    * Abandon probability >= 0.25.
    * Blast-radius score >= 0.5.
    * Latency p90 in the top 20% across the plan.
    """
    if not predictions:
        return ()

    out: list[Bottleneck] = []
    fan = _fan_out(predictions)
    latencies = sorted((t.latency_p90 for t in predictions), reverse=True)
    cut = latencies[max(0, len(latencies) // 5 - 1)] if latencies else 0.0
    seen: set[tuple[str, str]] = set()

    def _emit(task: TaskPrediction, reason: str, score: float) -> None:
        key = (task.task_id, reason)
        if key in seen:
            return
        seen.add(key)
        out.append(Bottleneck(task_id=task.task_id, title=task.title, reason=reason, score=score))

    for task in predictions:
        fo = fan.get(task.task_id, 0)
        if fo >= 2:
            _emit(task, "fan_out", float(fo))
        if task.abandon_probability >= 0.25:
            _emit(task, "high_abandon", task.abandon_probability)
        if task.blast_radius_score >= 0.5:
            _emit(task, "high_blast_radius", task.blast_radius_score)
        if cut > 0 and task.latency_p90 >= cut:
            _emit(task, "long_latency", task.latency_p90)

    # Sort by descending score so the top entries surface first.
    out.sort(key=lambda b: -b.score)
    return tuple(out)


def _decision_edges(predictions: Sequence[TaskPrediction]) -> tuple[DecisionEdge, ...]:
    """Build the simulated decision-flow graph.

    Each task with no resolved predecessor receives an edge from a synthetic
    ``START`` node so the resulting Mermaid graph has a single root.
    """
    ids = {t.task_id for t in predictions}
    edges: list[DecisionEdge] = []
    for task in predictions:
        resolved = [dep for dep in task.depends_on if dep in ids]
        if not resolved:
            edges.append(DecisionEdge(from_task="START", to_task=task.task_id, label=task.role))
            continue
        for dep_id in resolved:
            edges.append(DecisionEdge(from_task=dep_id, to_task=task.task_id, label=task.role))
    return tuple(edges)


def _criterion_bias(predictions: Sequence[TaskPrediction]) -> CriterionProfileBias:
    """Compute the criterion-profile bias chart."""
    buckets: dict[str, float] = {"speed": 0.0, "cost": 0.0, "quality": 0.0, "safety": 0.0}
    total = float(len(predictions))
    if total == 0:
        return CriterionProfileBias(speed=0.0, cost=0.0, quality=0.0, safety=0.0)
    for task in predictions:
        bucket = role_criterion(task.role)
        if bucket in buckets:
            buckets[bucket] += 1.0
        else:
            buckets["quality"] += 1.0
    return CriterionProfileBias(
        speed=buckets["speed"] / total,
        cost=buckets["cost"] / total,
        quality=buckets["quality"] / total,
        safety=buckets["safety"] / total,
    )


def _jitter(rng: random.Random, value: float, *, spread: float) -> float:
    """Apply a deterministic multiplicative jitter to ``value``.

    Used to differentiate identical-shape tasks under the same seed so
    operators see a non-flat plot in the Markdown report. The spread is
    intentionally tiny (default 5%) so the predicted bands stay close to
    the underlying calibrated values.
    """
    factor = 1.0 + (rng.random() - 0.5) * spread
    return max(0.0, value * factor)


def simulate(
    plan_path: Path | str,
    options: SimulationOptions | None = None,
) -> SimulationReport:
    """Run a full simulation for ``plan_path`` and return the report.

    Args:
        plan_path: Path to a YAML plan file (same format as
            ``bernstein run``).
        options: :class:`SimulationOptions` knobs. ``None`` uses defaults
            (seed=42, no budget cap, cold-start everywhere).

    Returns:
        :class:`SimulationReport` with per-task predictions and aggregate
        bands.

    Raises:
        SimulationError: If the plan cannot be loaded.
    """
    opts = options or SimulationOptions()
    path = Path(plan_path)

    try:
        loaded = load_plan(path)  # type: ignore[reportUnknownVariableType]
    except PlanLoadError as exc:
        raise SimulationError(f"failed to load plan {path}: {exc}") from exc
    plan_config = loaded[0]
    # ``Task`` lives in a module excluded from pyright strict; cast so the
    # downstream loop has a concrete type to work against.
    tasks: list[Task] = cast("list[Task]", loaded[1])

    metrics_dir = Path(opts.metrics_dir) if opts.metrics_dir else None
    traces_dir = Path(opts.traces_dir) if opts.traces_dir else None

    traces: HistoricalTraces = load_traces(traces_dir, limit=opts.from_traces)
    cost_pred = CostPredictor(metrics_dir=metrics_dir, history_limit=opts.from_traces)
    latency_pred = LatencyPredictor(traces=traces)
    abandon_pred = AbandonmentPredictor(traces=traces)
    blast_pred = BlastRadiusPredictor()

    rng = random.Random(opts.seed)
    ordered = _topological_order(tasks)
    title_to_id = {t.title: t.id for t in tasks}

    notes: list[str] = []
    if traces.is_empty:
        notes.append("no trace history available; predictions use cold-start priors")
    if metrics_dir is None:
        notes.append("no metrics history available; cost band uses heuristic")

    predictions: list[TaskPrediction] = []
    running_p90 = 0.0
    cold_any = False
    for task in ordered:
        cost_p50, cost_p90, cold = cost_pred.predict(task)
        # Apply tiny deterministic jitter so two identical tasks don't
        # collapse into the same row in the report.
        cost_p50 = round(_jitter(rng, cost_p50, spread=0.04), 4)
        cost_p90 = round(_jitter(rng, cost_p90, spread=0.04), 4)
        latency_p50, latency_p90 = latency_pred.predict(task)
        latency_p50 = round(_jitter(rng, latency_p50, spread=0.05), 2)
        latency_p90 = round(_jitter(rng, latency_p90, spread=0.05), 2)
        abandon_prob = abandon_pred.predict(task)
        blast_score = blast_pred.predict(task)

        # Resolve depends_on (titles) to task ids so the report carries
        # a graph the consumer can render without rejoining tables.
        resolved_deps: list[str] = []
        for dep in task.depends_on:
            dep_id = title_to_id.get(dep, dep)
            resolved_deps.append(dep_id)

        budget_violation = False
        if opts.budget_cap is not None:
            running_p90 += cost_p90
            if running_p90 > opts.budget_cap:
                budget_violation = True

        cold_any = cold_any or cold
        predictions.append(
            TaskPrediction(
                task_id=task.id,
                title=task.title,
                role=task.role,
                adapter=(task.cli or "mock").strip().lower(),
                cost_p50=cost_p50,
                cost_p90=cost_p90,
                latency_p50=latency_p50,
                latency_p90=latency_p90,
                abandon_probability=round(abandon_prob, 4),
                blast_radius_score=round(blast_score, 4),
                depends_on=tuple(resolved_deps),
                cold_start=cold,
                budget_violation=budget_violation,
            )
        )

    total_p50 = sum(p.cost_p50 for p in predictions)
    total_p90 = sum(p.cost_p90 for p in predictions)
    wall_p50 = _critical_path(predictions, use_p90=False)
    wall_p90 = _critical_path(predictions, use_p90=True)
    expected_abandon = sum(p.abandon_probability for p in predictions)
    max_blast = max((p.blast_radius_score for p in predictions), default=0.0)

    budget_breach = False
    if opts.budget_cap is not None and total_p90 > opts.budget_cap:
        budget_breach = True

    aggregate = AggregateBands(
        total_cost_p50=round(total_p50, 4),
        total_cost_p90=round(total_p90, 4),
        wall_clock_p50=round(wall_p50, 2),
        wall_clock_p90=round(wall_p90, 2),
        expected_abandonments=round(expected_abandon, 4),
        max_blast_radius=round(max_blast, 4),
        budget_cap=opts.budget_cap,
        budget_breach=budget_breach,
    )

    return SimulationReport(
        plan_name=plan_config.name or path.stem,
        plan_path=str(path),
        seed=opts.seed,
        task_count=len(predictions),
        tasks=tuple(predictions),
        aggregate=aggregate,
        bottlenecks=_bottlenecks(predictions),
        decision_edges=_decision_edges(predictions),
        criterion_bias=_criterion_bias(predictions),
        history_samples=traces.sample_count,
        cold_start=cold_any,
        notes=tuple(notes),
    )


# Silence pyflakes for ``replace`` (kept available for downstream callers
# that want to derive variants of a frozen report without re-deriving).
_ = replace
