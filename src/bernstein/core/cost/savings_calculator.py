from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bernstein.core.cost.cost import compute_savings_vs_opus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bernstein.core.observability.metric_collector import TaskMetrics


@dataclass(frozen=True)
class SavingsReport:
    """Derived end-of-run savings metrics for the summary card."""

    sequential_time_seconds: float
    parallel_time_seconds: float
    time_saved_seconds: float
    time_saved_pct: float
    total_cost_usd: float
    cost_per_task_usd: float
    routing_savings_usd: float


def calculate_savings(
    task_metrics: Iterable[TaskMetrics],
    *,
    wall_clock_seconds: float,
    total_cost_usd: float,
    completed_tasks: int,
) -> SavingsReport:
    """Calculate time/cost savings for a finished run.

    Sequential time is the sum of completed task durations.
    Parallel time is the actual wall clock for the run.
    Routing savings compares actual model spend with an all-Opus baseline.
    """

    completed = [m for m in task_metrics if m.end_time is not None]
    sequential_time_seconds = sum(max(float(m.end_time or 0.0) - float(m.start_time), 0.0) for m in completed)
    parallel_time_seconds = max(wall_clock_seconds, 0.0)
    if sequential_time_seconds <= 0.0:
        sequential_time_seconds = parallel_time_seconds
    time_saved_seconds = max(sequential_time_seconds - parallel_time_seconds, 0.0)
    time_saved_pct = (time_saved_seconds / sequential_time_seconds) if sequential_time_seconds > 0 else 0.0
    cost_per_task_usd = (total_cost_usd / completed_tasks) if completed_tasks > 0 else 0.0
    routing_savings_usd = compute_savings_vs_opus(
        [
            {
                "cost_usd": m.cost_usd,
                "tokens_prompt": m.tokens_prompt,
                "tokens_completion": m.tokens_completion,
                "model": m.model,
            }
            for m in completed
        ]
    )
    return SavingsReport(
        sequential_time_seconds=sequential_time_seconds,
        parallel_time_seconds=parallel_time_seconds,
        time_saved_seconds=time_saved_seconds,
        time_saved_pct=time_saved_pct,
        total_cost_usd=total_cost_usd,
        cost_per_task_usd=cost_per_task_usd,
        routing_savings_usd=routing_savings_usd,
    )
