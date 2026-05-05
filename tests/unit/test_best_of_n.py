"""Tests for the recursive best-of-N delegation pipeline."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from bernstein.core import defaults as _defaults
from bernstein.core.orchestration.best_of_n import (
    BestOfNRunner,
    CandidateResult,
    ScoreWeights,
    clamp_n,
    is_best_of_n,
    judge_candidates,
    score_candidate,
    select_best,
    task_n,
)
from bernstein.core.orchestration.tick_pipeline import partition_best_of_n
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus, TaskType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(*, task_id: str = "parent-1", best_of_n: int | None = None) -> Task:
    return Task(
        id=task_id,
        title="title",
        description="desc",
        role="backend",
        priority=2,
        scope=Scope.MEDIUM,
        complexity=Complexity.MEDIUM,
        status=TaskStatus.OPEN,
        task_type=TaskType.STANDARD,
        best_of_n=best_of_n,
    )


def _candidate(
    task_id: str,
    *,
    diff: str = "diff",
    tests: bool = True,
    lint: float = 1.0,
    runtime: float = 60.0,
    judge: float | None = None,
    worktree: str = "",
) -> CandidateResult:
    return CandidateResult(
        task_id=task_id,
        diff=diff,
        tests_passing=tests,
        lint_score=lint,
        runtime_s=runtime,
        judge_score=judge,
        worktree_path=worktree,
    )


@pytest.fixture(autouse=True)
def _reset_best_of_n_defaults() -> Generator[None, None, None]:
    _defaults.reset()
    _defaults.override("best_of_n", {"enabled": True})
    yield
    _defaults.reset()


# ---------------------------------------------------------------------------
# Defaults & opt-in
# ---------------------------------------------------------------------------


def test_default_flag_is_off_when_reset() -> None:
    _defaults.reset()
    assert _defaults.BEST_OF_N.enabled is False
    assert _defaults.BEST_OF_N.max_candidates == 5
    task = _make_task(best_of_n=3)
    assert is_best_of_n(task) is False


def test_clamp_n_collapses_low_values() -> None:
    assert clamp_n(0) == 1
    assert clamp_n(1) == 1
    assert clamp_n(3) == 3
    assert clamp_n(10) == _defaults.BEST_OF_N.max_candidates


def test_task_n_zero_for_legacy_task() -> None:
    assert task_n(_make_task()) == 1


def test_task_n_clamped_when_too_large() -> None:
    big = _make_task(best_of_n=99)
    assert task_n(big) == _defaults.BEST_OF_N.max_candidates


def test_is_best_of_n_requires_global_flag() -> None:
    _defaults.override("best_of_n", {"enabled": False})
    assert is_best_of_n(_make_task(best_of_n=3)) is False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_score_candidate_perfect_signals_no_judge() -> None:
    cand = _candidate("a", tests=True, lint=1.0, runtime=0.0, judge=None)
    assert score_candidate(cand) == pytest.approx(1.0)


def test_score_candidate_failing_tests_dominates() -> None:
    passing = _candidate("p", tests=True, lint=0.0, runtime=1800.0)
    failing = _candidate("f", tests=False, lint=1.0, runtime=0.0)
    assert score_candidate(passing) > score_candidate(failing)


def test_score_candidate_judge_redistribution() -> None:
    cand = _candidate("c", tests=True, lint=1.0, runtime=0.0, judge=None)
    cand_with_judge = _candidate("c", tests=True, lint=1.0, runtime=0.0, judge=1.0)
    assert score_candidate(cand) == pytest.approx(score_candidate(cand_with_judge))


def test_score_candidate_runtime_decays_linearly() -> None:
    fast = _candidate("f", runtime=0.0, tests=True, lint=1.0)
    slow = _candidate("s", runtime=1800.0, tests=True, lint=1.0)
    assert score_candidate(fast) > score_candidate(slow)


def test_score_clamps_invalid_lint_score() -> None:
    cand = _candidate("c", lint=2.0, tests=True, runtime=0.0)
    assert score_candidate(cand) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Winner selection
# ---------------------------------------------------------------------------


def test_select_best_picks_highest_score() -> None:
    losers = _candidate("loser", tests=False, lint=0.5, runtime=120.0)
    winner = _candidate("winner", tests=True, lint=1.0, runtime=30.0, judge=0.9)
    assert select_best([losers, winner]).task_id == "winner"


def test_select_best_breaks_ties_via_passing_then_runtime() -> None:
    a = _candidate("a", tests=True, lint=1.0, runtime=30.0, judge=0.5)
    b = _candidate("b", tests=True, lint=1.0, runtime=10.0, judge=0.5)
    assert select_best([a, b]).task_id == "b"


def test_select_best_empty_raises() -> None:
    with pytest.raises(ValueError):
        select_best([])


# ---------------------------------------------------------------------------
# Judge integration
# ---------------------------------------------------------------------------


def test_judge_candidates_returns_input_when_callback_none() -> None:
    cands = [_candidate("a"), _candidate("b")]
    out = judge_candidates(cands, "rubric", judge=None)
    assert out == cands


def test_judge_candidates_invokes_callback_only_for_non_empty_diffs() -> None:
    seen: list[list[CandidateResult]] = []

    def judge(cands: list[CandidateResult], rubric: str) -> list[CandidateResult]:
        seen.append(cands)
        return [
            CandidateResult(
                task_id=c.task_id,
                diff=c.diff,
                tests_passing=c.tests_passing,
                lint_score=c.lint_score,
                runtime_s=c.runtime_s,
                judge_score=0.7,
            )
            for c in cands
        ]

    cands = [_candidate("good", diff="patch"), _candidate("empty", diff="")]
    out = judge_candidates(cands, "rubric", judge=judge)
    assert seen == [[cands[0]]]
    by_id = {c.task_id: c for c in out}
    assert by_id["good"].judge_score == pytest.approx(0.7)
    assert by_id["empty"].judge_score == pytest.approx(0.0)


def test_judge_candidates_disabled_via_defaults() -> None:
    _defaults.override("best_of_n", {"enabled": True, "judge_enabled": False})

    def judge(_cands: list[CandidateResult], _rubric: str) -> list[CandidateResult]:
        raise AssertionError("judge must not be invoked when flag is off")

    cands = [_candidate("a")]
    assert judge_candidates(cands, "rubric", judge=judge) == cands


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_runner_spawns_clamped_n_and_picks_winner() -> None:
    spawned: list[tuple[str, int]] = []

    def spawner(parent: Task, n: int) -> list[str]:
        spawned.append((parent.id, n))
        return [f"{parent.id}-c{i}" for i in range(n)]

    def awaiter(ids: list[str]) -> list[CandidateResult]:
        return [
            _candidate(ids[0], tests=True, lint=1.0, runtime=10.0, diff="patch", worktree="/tmp/c0"),
            _candidate(ids[1], tests=False, lint=0.5, runtime=120.0, diff="", worktree="/tmp/c1"),
            _candidate(ids[2], tests=True, lint=0.7, runtime=200.0, diff="patch", worktree="/tmp/c2"),
        ]

    reclaimed: list[CandidateResult] = []

    def reclaimer(loser: CandidateResult) -> None:
        reclaimed.append(loser)

    runner = BestOfNRunner(spawner=spawner, awaiter=awaiter, reclaimer=reclaimer)
    outcome = runner.run(_make_task(), n=3)

    assert spawned == [("parent-1", 3)]
    assert outcome.winner.task_id.endswith("-c0")
    assert outcome.n_requested == 3
    assert outcome.n_actual == 3
    assert {r.task_id for r in reclaimed} == {
        outcome.candidates[1].task_id,
        outcome.candidates[2].task_id,
    }


def test_runner_invokes_judge_callback_when_provided() -> None:
    def spawner(_p: Task, n: int) -> list[str]:
        return [f"c{i}" for i in range(n)]

    def awaiter(ids: list[str]) -> list[CandidateResult]:
        return [_candidate(i, tests=True, lint=1.0, diff="patch") for i in ids]

    invoked: dict[str, int] = {"count": 0}

    def judge(cands: list[CandidateResult], _rubric: str) -> list[CandidateResult]:
        invoked["count"] += 1
        return [
            CandidateResult(
                task_id=c.task_id,
                diff=c.diff,
                tests_passing=c.tests_passing,
                lint_score=c.lint_score,
                runtime_s=c.runtime_s,
                judge_score=1.0 if c.task_id == "c1" else 0.1,
            )
            for c in cands
        ]

    runner = BestOfNRunner(spawner=spawner, awaiter=awaiter, judge=judge)
    outcome = runner.run(_make_task(), n=2)
    assert invoked["count"] == 1
    assert outcome.winner.task_id == "c1"
    assert outcome.winner.judge_score == pytest.approx(1.0)


def test_runner_rejects_inconsistent_spawner_count() -> None:
    def spawner(_p: Task, _n: int) -> list[str]:
        return ["only-one"]

    def awaiter(_ids: list[str]) -> list[CandidateResult]:
        return []

    runner = BestOfNRunner(spawner=spawner, awaiter=awaiter)
    with pytest.raises(ValueError, match="expected exactly"):
        runner.run(_make_task(), n=3)


def test_runner_zero_results_raises() -> None:
    def spawner(_p: Task, n: int) -> list[str]:
        return [f"c{i}" for i in range(n)]

    def awaiter(_ids: list[str]) -> list[CandidateResult]:
        return []

    runner = BestOfNRunner(spawner=spawner, awaiter=awaiter)
    with pytest.raises(RuntimeError, match="zero candidate"):
        runner.run(_make_task(), n=2)


def test_runner_clamps_oversized_n() -> None:
    seen_n: list[int] = []

    def spawner(_p: Task, n: int) -> list[str]:
        seen_n.append(n)
        return [f"c{i}" for i in range(n)]

    def awaiter(ids: list[str]) -> list[CandidateResult]:
        return [_candidate(i, tests=True, diff="x") for i in ids]

    runner = BestOfNRunner(spawner=spawner, awaiter=awaiter)
    runner.run(_make_task(), n=99)
    assert seen_n == [_defaults.BEST_OF_N.max_candidates]


def test_runner_skips_reclaim_when_callback_none() -> None:
    def spawner(_p: Task, n: int) -> list[str]:
        return [f"c{i}" for i in range(n)]

    def awaiter(ids: list[str]) -> list[CandidateResult]:
        return [_candidate(i, tests=True, diff="x") for i in ids]

    runner = BestOfNRunner(spawner=spawner, awaiter=awaiter, reclaimer=None)
    outcome = runner.run(_make_task(), n=2)
    assert len(outcome.losers) == 1


# ---------------------------------------------------------------------------
# tick_pipeline routing helper
# ---------------------------------------------------------------------------


def test_partition_best_of_n_separates_opted_in_tasks() -> None:
    legacy = _make_task(task_id="legacy")
    fan_out = _make_task(task_id="fan", best_of_n=3)
    single, parallel = partition_best_of_n([legacy, fan_out])
    assert [t.id for t in single] == ["legacy"]
    assert [(t.id, n) for t, n in parallel] == [("fan", 3)]


def test_partition_best_of_n_respects_global_flag() -> None:
    _defaults.override("best_of_n", {"enabled": False})
    fan_out = _make_task(task_id="fan", best_of_n=3)
    single, parallel = partition_best_of_n([fan_out])
    assert [t.id for t in single] == ["fan"]
    assert parallel == []


# ---------------------------------------------------------------------------
# Custom weights
# ---------------------------------------------------------------------------


def test_custom_weights_can_invert_priority() -> None:
    runtime_first = ScoreWeights(tests=0.0, lint=0.0, judge=0.0, runtime=1.0)
    fast_failing = _candidate("fast", tests=False, lint=0.0, runtime=0.0)
    slow_passing = _candidate("slow", tests=True, lint=1.0, runtime=1800.0)
    assert select_best([fast_failing, slow_passing], runtime_first).task_id == "fast"
