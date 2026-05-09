"""A/B runner primitive — deterministic prompt-vs-prompt comparison.

This is the *primitive* layer for KF-9 (eval harness + A/B). It runs two
prompt variants over the same task set, scores each output, and produces a
deterministic comparison artefact (JSON-serialisable). Synthetic / dummy
executors are first-class so this slice has zero LLM-cost test path.

Design notes:
    * Pure functions; no I/O coupling beyond explicit ``executor`` /
      ``scorer`` callables passed in.
    * Deterministic ordering: tasks iterate in input order; comparison
      output uses ``sort_keys`` JSON dump for stable diffs.
    * Companion to ``bernstein.core.quality.ab_test`` (model-vs-model on a
      single live task via httpx). This module covers prompt-vs-prompt
      offline / synthetic eval.
    * Benchmark loaders (SWE-bench Pro, Terminal-Bench) are intentionally
      out of scope — see ``feat-swe-bench-pro-terminal-bench-nightly``.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Variant:
    """A prompt or model variant under test.

    Attributes:
        name: Human-readable variant id (e.g. ``"reviewer-v1"``).
        prompt: Prompt template / system prompt body.
        model: Optional model hint (``"haiku"`` etc.); not interpreted by
            the runner — surfaced for downstream executors.
        metadata: Free-form extra fields persisted into the comparison.
    """

    name: str
    prompt: str
    model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    """A single evaluation task.

    Attributes:
        task_id: Stable identifier (used for grouping in the comparison).
        input: Task input — typed as ``Any`` so YAML / synthetic / real
            payloads all flow through.
        expected: Optional reference answer used by deterministic scorers.
    """

    task_id: str
    input: Any
    expected: Any = None


@dataclass(frozen=True)
class RunResult:
    """One executor invocation's outcome for a (variant, task) pair.

    Attributes:
        variant: Variant name that produced ``output``.
        task_id: Task that was executed.
        output: Raw executor output (string, dict, …).
        score: Normalised score in ``[0.0, 1.0]``.
        duration_ms: Wall-clock duration in milliseconds.
        passed: Whether the task is considered successful (score >= 0.5
            by default; can be overridden by the scorer).
    """

    variant: str
    task_id: str
    output: Any
    score: float
    duration_ms: float = 0.0
    passed: bool = False


@dataclass(frozen=True)
class VariantStats:
    """Aggregate stats for one variant across the task set.

    Attributes:
        name: Variant name.
        n: Total task count for this variant.
        pass_count: Number of tasks where ``passed`` is True.
        pass_rate: ``pass_count / n`` (0.0 when n=0).
        mean_score: Arithmetic mean of per-task scores.
        mean_duration_ms: Arithmetic mean of per-task durations.
    """

    name: str
    n: int
    pass_count: int
    pass_rate: float
    mean_score: float
    mean_duration_ms: float


@dataclass(frozen=True)
class TaskDelta:
    """Per-task score delta between A and B (B - A).

    Attributes:
        task_id: Task identifier.
        score_a: Variant A's score (NaN-safe: 0.0 if missing).
        score_b: Variant B's score.
        delta: ``score_b - score_a``; positive => B beats A.
    """

    task_id: str
    score_a: float
    score_b: float
    delta: float


@dataclass(frozen=True)
class Comparison:
    """A/B comparison artefact — the deliverable of this primitive.

    Attributes:
        variant_a: Stats for the A side.
        variant_b: Stats for the B side.
        per_task: Per-task deltas, in input order.
        winner: ``"a"``, ``"b"``, or ``"tie"`` based on pass-rate then
            mean-score (5% tolerance band).
        reason: Human-readable explanation.
    """

    variant_a: VariantStats
    variant_b: VariantStats
    per_task: tuple[TaskDelta, ...]
    winner: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic dict suitable for JSON serialisation."""
        return {
            "variant_a": _stats_to_dict(self.variant_a),
            "variant_b": _stats_to_dict(self.variant_b),
            "per_task": [_delta_to_dict(d) for d in self.per_task],
            "winner": self.winner,
            "reason": self.reason,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Render as a deterministic JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


# ---------------------------------------------------------------------------
# Executor / scorer protocols (typed callables, no Protocol class needed)
# ---------------------------------------------------------------------------


Executor = Callable[[Variant, Task], RunResult]
"""Synchronous executor: run one variant on one task, return RunResult.

Implementations should set ``score`` and ``passed`` themselves *or* leave
them at default (0.0 / False) and rely on the scorer.
"""

Scorer = Callable[[Variant, Task, Any], tuple[float, bool]]
"""Optional scorer: ``(variant, task, raw_output) -> (score, passed)``.

When supplied to :func:`run_ab`, the scorer runs after the executor and
overwrites the executor's score / passed. Use this for deterministic
post-hoc grading.
"""


# ---------------------------------------------------------------------------
# Built-in scorers (synthetic-friendly)
# ---------------------------------------------------------------------------


def exact_match_scorer(_variant: Variant, task: Task, output: Any) -> tuple[float, bool]:
    """Score 1.0 iff ``str(output) == str(task.expected)``.

    Args:
        _variant: Unused (kept for protocol compatibility).
        task: The task being scored — uses ``task.expected``.
        output: Raw executor output.

    Returns:
        ``(1.0, True)`` on exact-string match, else ``(0.0, False)``.
    """
    matched = str(output) == str(task.expected)
    return (1.0 if matched else 0.0, matched)


# ---------------------------------------------------------------------------
# Built-in executor for tests / dry-runs
# ---------------------------------------------------------------------------


def echo_executor(variant: Variant, task: Task) -> RunResult:
    """Deterministic dummy executor — returns ``f"{prompt}::{input}"``.

    Used by the test fixtures and as a smoke-test default. Score is left
    at 0.0; pair with a scorer (e.g. :func:`exact_match_scorer`) to make
    a meaningful comparison.

    Args:
        variant: Variant whose prompt is echoed.
        task: Task whose input is echoed.

    Returns:
        ``RunResult`` with deterministic ``output`` and zero duration.
    """
    output = f"{variant.prompt}::{task.input}"
    return RunResult(
        variant=variant.name,
        task_id=task.task_id,
        output=output,
        score=0.0,
        duration_ms=0.0,
        passed=False,
    )


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run_ab(
    variant_a: Variant,
    variant_b: Variant,
    tasks: Iterable[Task],
    *,
    executor: Executor = echo_executor,
    scorer: Scorer | None = None,
    tolerance: float = 0.05,
) -> Comparison:
    """Run two variants over the same task set and build a comparison.

    Tasks are iterated once; each task is executed for variant A then
    variant B. Results are aggregated into :class:`VariantStats` and
    per-task deltas are computed in input order.

    Args:
        variant_a: First variant (the baseline / control).
        variant_b: Second variant (the candidate / treatment).
        tasks: Iterable of :class:`Task`. Consumed once.
        executor: Callable that runs one variant on one task. Defaults to
            :func:`echo_executor` for synthetic test paths.
        scorer: Optional callable that overrides executor scoring with a
            deterministic post-hoc grade.
        tolerance: Pass-rate / mean-score tolerance band for tie-calling.
            Default 5% (``0.05``).

    Returns:
        A populated :class:`Comparison`.

    Raises:
        ValueError: If ``variant_a.name == variant_b.name`` (would cause
            ambiguous deltas).
    """
    if variant_a.name == variant_b.name:
        msg = f"variant names must differ; got {variant_a.name!r} twice"
        raise ValueError(msg)

    task_list = list(tasks)
    results_a: list[RunResult] = []
    results_b: list[RunResult] = []

    for task in task_list:
        results_a.append(_score_one(variant_a, task, executor, scorer))
        results_b.append(_score_one(variant_b, task, executor, scorer))

    stats_a = _aggregate(variant_a.name, results_a)
    stats_b = _aggregate(variant_b.name, results_b)

    deltas: list[TaskDelta] = []
    by_task_a = {r.task_id: r for r in results_a}
    by_task_b = {r.task_id: r for r in results_b}
    for task in task_list:
        ra = by_task_a.get(task.task_id)
        rb = by_task_b.get(task.task_id)
        sa = ra.score if ra else 0.0
        sb = rb.score if rb else 0.0
        deltas.append(TaskDelta(task_id=task.task_id, score_a=sa, score_b=sb, delta=sb - sa))

    winner, reason = _decide_winner(stats_a, stats_b, tolerance=tolerance)

    return Comparison(
        variant_a=stats_a,
        variant_b=stats_b,
        per_task=tuple(deltas),
        winner=winner,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# YAML / JSON I/O helpers (thin wrappers — keep deps light)
# ---------------------------------------------------------------------------


def load_variant_yaml(path: Path) -> Variant:
    """Load a Variant from a YAML file with keys ``name``, ``prompt``.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed :class:`Variant`.

    Raises:
        ValueError: If required keys are missing.
    """
    import yaml  # local import: only needed for CLI / file path

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "name" not in raw or "prompt" not in raw:
        msg = f"variant YAML {path} missing required keys 'name'/'prompt'"
        raise ValueError(msg)
    return Variant(
        name=str(raw["name"]),
        prompt=str(raw["prompt"]),
        model=raw.get("model"),
        metadata=raw.get("metadata", {}) or {},
    )


def load_tasks_yaml(path: Path) -> list[Task]:
    """Load a list of Tasks from a YAML file with a top-level ``tasks`` key.

    Expected schema::

        tasks:
          - id: t1
            input: "hello"
            expected: "world"

    Args:
        path: Path to the YAML file.

    Returns:
        List of :class:`Task`, in YAML order.

    Raises:
        ValueError: If the file lacks a ``tasks`` list.
    """
    import yaml  # local import

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("tasks")
    if not isinstance(items, list):
        msg = f"tasks YAML {path} must have top-level 'tasks: [...]' list"
        raise ValueError(msg)
    return [
        Task(
            task_id=str(item.get("id", f"t{idx}")),
            input=item.get("input"),
            expected=item.get("expected"),
        )
        for idx, item in enumerate(items)
    ]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _score_one(
    variant: Variant,
    task: Task,
    executor: Executor,
    scorer: Scorer | None,
) -> RunResult:
    """Run executor and (optionally) override score with scorer."""
    res = executor(variant, task)
    if scorer is None:
        return res
    score, passed = scorer(variant, task, res.output)
    return RunResult(
        variant=res.variant,
        task_id=res.task_id,
        output=res.output,
        score=score,
        duration_ms=res.duration_ms,
        passed=passed,
    )


def _aggregate(name: str, results: list[RunResult]) -> VariantStats:
    """Compute :class:`VariantStats` from a list of run results."""
    n = len(results)
    if n == 0:
        return VariantStats(name=name, n=0, pass_count=0, pass_rate=0.0, mean_score=0.0, mean_duration_ms=0.0)
    pass_count = sum(1 for r in results if r.passed)
    return VariantStats(
        name=name,
        n=n,
        pass_count=pass_count,
        pass_rate=pass_count / n,
        mean_score=statistics.fmean(r.score for r in results),
        mean_duration_ms=statistics.fmean(r.duration_ms for r in results),
    )


def _decide_winner(
    a: VariantStats,
    b: VariantStats,
    *,
    tolerance: float,
) -> tuple[str, str]:
    """Pick winner from pass-rate (primary) then mean-score (secondary)."""
    pr_diff = b.pass_rate - a.pass_rate
    if abs(pr_diff) > tolerance:
        if pr_diff > 0:
            return "b", f"{b.name} pass_rate {b.pass_rate:.2%} beat {a.name} {a.pass_rate:.2%}"
        return "a", f"{a.name} pass_rate {a.pass_rate:.2%} beat {b.name} {b.pass_rate:.2%}"

    score_diff = b.mean_score - a.mean_score
    if abs(score_diff) > tolerance:
        if score_diff > 0:
            return "b", f"{b.name} mean_score {b.mean_score:.3f} beat {a.name} {a.mean_score:.3f}"
        return "a", f"{a.name} mean_score {a.mean_score:.3f} beat {b.name} {b.mean_score:.3f}"

    return "tie", f"variants within {tolerance:.0%} tolerance on pass-rate and mean-score"


def _stats_to_dict(s: VariantStats) -> dict[str, Any]:
    return {
        "name": s.name,
        "n": s.n,
        "pass_count": s.pass_count,
        "pass_rate": s.pass_rate,
        "mean_score": s.mean_score,
        "mean_duration_ms": s.mean_duration_ms,
    }


def _delta_to_dict(d: TaskDelta) -> dict[str, Any]:
    return {
        "task_id": d.task_id,
        "score_a": d.score_a,
        "score_b": d.score_b,
        "delta": d.delta,
    }


__all__ = [
    "Comparison",
    "Executor",
    "RunResult",
    "Scorer",
    "Task",
    "TaskDelta",
    "Variant",
    "VariantStats",
    "echo_executor",
    "exact_match_scorer",
    "load_tasks_yaml",
    "load_variant_yaml",
    "run_ab",
]
