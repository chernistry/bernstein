from __future__ import annotations

from bernstein.core.cost.savings_calculator import calculate_savings
from bernstein.core.observability.metric_collector import TaskMetrics


def _task(
    task_id: str,
    *,
    start: float,
    end: float,
    model: str = "sonnet",
    cost_usd: float = 1.0,
    tokens_prompt: int = 1000,
    tokens_completion: int = 1000,
) -> TaskMetrics:
    tm = TaskMetrics(
        task_id=task_id,
        role="backend",
        model=model,
        provider="anthropic",
        start_time=start,
    )
    tm.end_time = end
    tm.cost_usd = cost_usd
    tm.tokens_prompt = tokens_prompt
    tm.tokens_completion = tokens_completion
    return tm


def test_calculate_savings_uses_sum_of_task_durations() -> None:
    report = calculate_savings(
        [
            _task(
                "t1", start=0.0, end=60.0, model="haiku", cost_usd=0.02, tokens_prompt=10000, tokens_completion=10000
            ),
            _task(
                "t2", start=10.0, end=100.0, model="sonnet", cost_usd=0.08, tokens_prompt=10000, tokens_completion=10000
            ),
        ],
        wall_clock_seconds=100.0,
        total_cost_usd=0.1,
        completed_tasks=2,
    )

    assert report.sequential_time_seconds == 150.0
    assert report.parallel_time_seconds == 100.0
    assert report.time_saved_seconds == 50.0
    assert report.time_saved_pct == 50.0 / 150.0
    assert report.cost_per_task_usd == 0.05
    assert report.routing_savings_usd > 0.0


def test_calculate_savings_handles_empty_metrics() -> None:
    report = calculate_savings(
        [],
        wall_clock_seconds=42.0,
        total_cost_usd=0.0,
        completed_tasks=0,
    )

    assert report.sequential_time_seconds == 42.0
    assert report.time_saved_seconds == 0.0
    assert report.time_saved_pct == 0.0
    assert report.cost_per_task_usd == 0.0
    assert report.routing_savings_usd == 0.0
