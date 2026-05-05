"""Recursive best-of-N delegation: spawn K candidates and pick the winner.

Implements the *recursive-best-of-N-delegation* agentic pattern.  For tasks
flagged with ``best_of_n=K`` (K in [2, 5]) the orchestrator spawns K
candidate workers in isolated worktrees, scores each candidate with
automated signals (tests passing, lint clean, runtime), optionally asks a
cheap-tier LLM judge for a rubric score, blends the two into a final
score, and keeps only the winner's diff.  Losing candidates' worktrees are
reclaimed.

Opt-in: callers must explicitly invoke :class:`BestOfNRunner`.  Tasks
opt in by setting ``Task.best_of_n=K``; tasks without that field run as a
single agent via the existing pipeline.  See
:data:`bernstein.core.defaults.BEST_OF_N` for the global enable flag.

Coexistence with :mod:`bernstein.core.orchestration.phase_pipeline`:
both patterns are independent — a task can be phased *or* fan-out, not
both at once (the runner only inspects ``best_of_n``).

Pattern source:
    https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/recursive-best-of-n-delegation.md
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bernstein.core import defaults as _defaults

if TYPE_CHECKING:
    from bernstein.core.tasks.models import Task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateResult:
    """Outcome of one parallel candidate worker.

    Attributes:
        task_id: Server-assigned identifier for the candidate subtask.
        diff: Unified-diff text produced by the candidate, or empty when
            the candidate failed to make changes.
        tests_passing: Whether the post-implementation test run was green.
        lint_score: 0.0-1.0; 1.0 means zero lint findings.
        runtime_s: Wall-clock seconds the candidate took.
        judge_score: Optional 0.0-1.0 LLM-as-judge rubric score.  ``None``
            means the judge was not invoked (e.g. judge disabled or the
            candidate produced an empty diff).
        worktree_path: Filesystem path to the candidate's isolated
            worktree, used by :func:`reclaim_losers` for cleanup.
    """

    task_id: str
    diff: str = ""
    tests_passing: bool = False
    lint_score: float = 0.0
    runtime_s: float = 0.0
    judge_score: float | None = None
    worktree_path: str = ""


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreWeights:
    """Weights used by :func:`score_candidate` and :func:`select_best`.

    The defaults are tuned so that a candidate with passing tests
    outranks a faster candidate that left tests broken, while a clean
    diff still tips a near-tie towards the simpler change.
    """

    tests: float = 0.5
    lint: float = 0.2
    judge: float = 0.2
    runtime: float = 0.1


_DEFAULT_WEIGHTS = ScoreWeights()


def score_candidate(result: CandidateResult, weights: ScoreWeights = _DEFAULT_WEIGHTS) -> float:
    """Compute a 0.0-1.0 automated score for *result*.

    Combines tests-passing, lint score, runtime (normalised so faster is
    better), and the judge score when available.  When the judge has not
    been invoked the judge weight is redistributed proportionally across
    the remaining components — a candidate with no judge score is not
    penalised.
    """
    tests_signal = 1.0 if result.tests_passing else 0.0
    lint_signal = max(0.0, min(1.0, result.lint_score))
    runtime_signal = _runtime_signal(result.runtime_s)

    if result.judge_score is None:
        denom = weights.tests + weights.lint + weights.runtime
        if denom <= 0.0:
            return 0.0
        return (weights.tests * tests_signal + weights.lint * lint_signal + weights.runtime * runtime_signal) / denom

    judge_signal = max(0.0, min(1.0, result.judge_score))
    denom = weights.tests + weights.lint + weights.runtime + weights.judge
    if denom <= 0.0:
        return 0.0
    return (
        weights.tests * tests_signal
        + weights.lint * lint_signal
        + weights.runtime * runtime_signal
        + weights.judge * judge_signal
    ) / denom


def _runtime_signal(seconds: float) -> float:
    """Normalise runtime so faster candidates score higher.

    Maps 0s → 1.0 and decays linearly to 0.0 at 30 minutes.  Above the
    cap the signal is clamped at 0.0 — a 31-minute candidate is no worse
    than a 30-minute one for ranking purposes.
    """
    if seconds <= 0.0:
        return 1.0
    cap = 1800.0
    if seconds >= cap:
        return 0.0
    return 1.0 - (seconds / cap)


# ---------------------------------------------------------------------------
# Pluggable callbacks
# ---------------------------------------------------------------------------


CandidateSpawner = Callable[["Task", int], list[str]]
"""Spawn *n* isolated candidate subtasks for a parent task.

Concrete implementations create ``n`` worktrees, register subtasks with
the task store, and return the new task IDs.  The runner is
agent-agnostic — pass any callable matching this signature.  A typical
production wiring delegates to :func:`bernstein.core.orchestration.spawner`.
"""


CandidateAwaiter = Callable[[list[str]], list[CandidateResult]]
"""Block until all listed subtasks complete, then collect their results."""


JudgeCallback = Callable[[list[CandidateResult], str], list[CandidateResult]]
"""Score candidates with an LLM judge against *rubric*.

Implementations call the cheap-tier LLM via the cascade router, parse a
0.0-1.0 score per candidate, and return new :class:`CandidateResult`
records with ``judge_score`` populated.  Order must match the input.
"""


WorktreeReclaimer = Callable[[CandidateResult], None]
"""Tear down a losing candidate's worktree."""


# ---------------------------------------------------------------------------
# Default rubric
# ---------------------------------------------------------------------------


_DEFAULT_RUBRIC = (
    "Score the candidate diff from 0.0 to 1.0 on a single line.\n"
    "Criteria: correctness vs. the task description, scope discipline\n"
    "(no out-of-scope edits), and code quality (readability, idiomatic\n"
    "use of project patterns).  Return only the float."
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def clamp_n(requested: int) -> int:
    """Clamp *requested* into the configured ``[1, BEST_OF_N.max_candidates]`` range.

    A value of 1 (or below) collapses best-of-N back to the legacy
    single-agent path, which is the safe default when callers receive
    untrusted input.
    """
    cfg = _defaults.BEST_OF_N
    if requested <= 1:
        return 1
    return min(requested, cfg.max_candidates)


def select_best(
    candidates: list[CandidateResult],
    weights: ScoreWeights = _DEFAULT_WEIGHTS,
) -> CandidateResult:
    """Pick the candidate with the highest blended score.

    Ties are broken by ``tests_passing`` (passing wins), then by faster
    runtime, finally by ``task_id`` for determinism.

    Raises:
        ValueError: When *candidates* is empty.
    """
    if not candidates:
        raise ValueError("select_best requires at least one candidate")
    return max(
        candidates,
        key=lambda c: (
            score_candidate(c, weights),
            c.tests_passing,
            -c.runtime_s,
            c.task_id,
        ),
    )


def judge_candidates(
    candidates: list[CandidateResult],
    rubric: str,
    *,
    judge: JudgeCallback | None,
) -> list[CandidateResult]:
    """Run *judge* on *candidates* and return updated records.

    Returns the input unchanged when *judge* is ``None`` or
    :data:`bernstein.core.defaults.BEST_OF_N` has the judge disabled.
    Empty-diff candidates short-circuit to ``judge_score=0.0`` without
    spending a judge call.
    """
    if judge is None or not _defaults.BEST_OF_N.judge_enabled:
        return list(candidates)
    if not candidates:
        return []
    judgeable = [c for c in candidates if c.diff]
    if not judgeable:
        return [
            CandidateResult(
                task_id=c.task_id,
                diff=c.diff,
                tests_passing=c.tests_passing,
                lint_score=c.lint_score,
                runtime_s=c.runtime_s,
                judge_score=0.0,
                worktree_path=c.worktree_path,
            )
            for c in candidates
        ]
    scored = judge(judgeable, rubric)
    by_id = {r.task_id: r for r in scored}
    out: list[CandidateResult] = []
    for c in candidates:
        if c.task_id in by_id:
            out.append(by_id[c.task_id])
        else:
            out.append(
                CandidateResult(
                    task_id=c.task_id,
                    diff=c.diff,
                    tests_passing=c.tests_passing,
                    lint_score=c.lint_score,
                    runtime_s=c.runtime_s,
                    judge_score=0.0,
                    worktree_path=c.worktree_path,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BestOfNOutcome:
    """Aggregated result of one best-of-N round.

    Attributes:
        winner: Highest-scoring candidate.
        losers: All other candidates, in original order.
        candidates: The full list (winner + losers) for telemetry/debug.
        n_requested: K originally requested by the caller.
        n_actual: Number of candidates that returned (may be < K if some
            crashed and the spawner reported failures).
    """

    winner: CandidateResult
    losers: list[CandidateResult]
    candidates: list[CandidateResult]
    n_requested: int
    n_actual: int


@dataclass
class BestOfNRunner:
    """Drive a task through K parallel candidates and pick the winner.

    Attributes:
        spawner: Spawn ``n`` candidate subtasks; returns their task IDs.
        awaiter: Block until candidates finish; returns
            :class:`CandidateResult` records.
        judge: Optional LLM-as-judge callback.  ``None`` disables judging.
        reclaimer: Optional worktree teardown callback for losing
            candidates.  ``None`` skips cleanup (useful in tests).
        weights: Score-blending weights.
        rubric: Judge prompt; defaults to a project-agnostic diff rubric.
    """

    spawner: CandidateSpawner
    awaiter: CandidateAwaiter
    judge: JudgeCallback | None = None
    reclaimer: WorktreeReclaimer | None = None
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    rubric: str = _DEFAULT_RUBRIC

    def run(self, parent: Task, n: int) -> BestOfNOutcome:
        """Execute *parent* through ``n`` parallel candidates.

        Raises:
            ValueError: When the spawner / awaiter return inconsistent IDs.
            RuntimeError: When zero candidates produce a result.
        """
        clamped = clamp_n(n)
        ids = list(self.spawner(parent, clamped))
        if len(ids) != clamped:
            raise ValueError(f"spawner returned {len(ids)} ids for n={clamped}; expected exactly {clamped}")
        raw = self.awaiter(ids)
        if not raw:
            raise RuntimeError(f"best-of-N for task {parent.id} produced zero candidate results")
        scored = judge_candidates(raw, self.rubric, judge=self.judge)
        winner = select_best(scored, self.weights)
        losers = [c for c in scored if c.task_id != winner.task_id]
        if self.reclaimer is not None:
            for loser in losers:
                try:
                    self.reclaimer(loser)
                except Exception:  # pragma: no cover - reclaim must never fail the round
                    logger.exception(
                        "best-of-N reclaim failed for candidate %s (worktree %s)",
                        loser.task_id,
                        loser.worktree_path,
                    )
        _record_metrics(parent, winner, losers)
        logger.info(
            "best-of-N for task %s: winner=%s score=%.3f losers=%d",
            parent.id,
            winner.task_id,
            score_candidate(winner, self.weights),
            len(losers),
        )
        return BestOfNOutcome(
            winner=winner,
            losers=losers,
            candidates=list(scored),
            n_requested=clamped,
            n_actual=len(scored),
        )


# ---------------------------------------------------------------------------
# Task opt-in helpers
# ---------------------------------------------------------------------------


def is_best_of_n(task: Task) -> bool:
    """Return True when *task* opts into best-of-N execution.

    Honours both the task-level ``best_of_n`` field and the global
    :data:`bernstein.core.defaults.BEST_OF_N.enabled` flag.  Either being
    falsy keeps the task on the legacy single-agent path.
    """
    if not _defaults.BEST_OF_N.enabled:
        return False
    n = getattr(task, "best_of_n", None)
    if n is None:
        return False
    try:
        return int(n) > 1
    except (TypeError, ValueError):
        return False


def task_n(task: Task) -> int:
    """Return the candidate count requested on *task*, clamped to bounds.

    Returns 1 when the task is not opted in — callers can treat that as
    "single agent, skip best-of-N".
    """
    if not is_best_of_n(task):
        return 1
    raw = getattr(task, "best_of_n", None)
    try:
        return clamp_n(int(raw or 1))
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _record_metrics(parent: Task, winner: CandidateResult, losers: list[CandidateResult]) -> None:
    """Emit Prometheus telemetry for one best-of-N round.

    Imported lazily so the module remains usable in test environments
    where the Prometheus client is not installed.
    """
    try:
        from bernstein.core.observability import prometheus as _p
    except Exception:  # pragma: no cover - metrics are advisory
        return
    counter = getattr(_p, "best_of_n_candidates_total", None)
    histogram = getattr(_p, "best_of_n_judge_score", None)
    role = getattr(parent, "role", "") or ""
    if counter is not None:
        try:
            counter.labels(outcome="winner", role=role).inc()
            for _ in losers:
                counter.labels(outcome="loser", role=role).inc()
        except Exception:  # pragma: no cover
            logger.debug("best-of-N counter emit failed", exc_info=True)
    if histogram is not None:
        for cand in (winner, *losers):
            score = cand.judge_score
            if score is None:
                continue
            try:
                histogram.labels(role=role).observe(score)
            except Exception:  # pragma: no cover
                logger.debug("best-of-N histogram emit failed", exc_info=True)


__all__ = [
    "BestOfNOutcome",
    "BestOfNRunner",
    "CandidateAwaiter",
    "CandidateResult",
    "CandidateSpawner",
    "JudgeCallback",
    "ScoreWeights",
    "WorktreeReclaimer",
    "clamp_n",
    "is_best_of_n",
    "judge_candidates",
    "score_candidate",
    "select_best",
    "task_n",
]
