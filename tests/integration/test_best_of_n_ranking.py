"""Integration tests for TOPSIS-driven best-of-N candidate selection.

These tests drive the full spawn -> await -> rank -> select path of
:class:`BestOfNRunner` with a stub spawner and stub awaiter so the
selection-by-profile machinery is exercised end to end, including the
no-op fallback when no profile is supplied (regression invariant from
issue #1347).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.best_of_n_rank_cmd import best_of_n_group
from bernstein.core import defaults as _defaults
from bernstein.core.orchestration.best_of_n import (
    BestOfNRunner,
    CandidateResult,
    select_best,
    select_winner,
)
from bernstein.core.orchestration.multi_criteria_rank import (
    Criterion,
    CriterionProfile,
    build_criterion_profile,
)
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus, TaskType


def _make_task(*, task_id: str = "parent", best_of_n: int | None = 3) -> Task:
    return Task(
        id=task_id,
        title="t",
        description="d",
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
    tests: bool = True,
    lint: float = 1.0,
    runtime: float = 60.0,
    judge: float | None = None,
) -> CandidateResult:
    return CandidateResult(
        task_id=task_id,
        diff="diff",
        tests_passing=tests,
        lint_score=lint,
        runtime_s=runtime,
        judge_score=judge,
        worktree_path=f"/tmp/{task_id}",
    )


@pytest.fixture(autouse=True)
def _reset_defaults() -> Generator[None, None, None]:
    _defaults.reset()
    _defaults.override("best_of_n", {"enabled": True, "judge_enabled": False})
    yield
    _defaults.reset()


def _runner_for(
    candidates: list[CandidateResult],
) -> BestOfNRunner:
    """Build a BestOfNRunner whose spawner/awaiter return *candidates*."""
    ids = [c.task_id for c in candidates]

    def spawn(_task: Task, n: int) -> list[str]:
        return ids[:n]

    def await_results(_ids: list[str]) -> list[CandidateResult]:
        return list(candidates)

    return BestOfNRunner(spawner=spawn, awaiter=await_results)


# ---------------------------------------------------------------------------
# Regression: no profile -> identical to legacy select_best
# ---------------------------------------------------------------------------


def test_no_profile_select_winner_matches_legacy_select_best() -> None:
    candidates = [
        _candidate("slow-clean", tests=True, lint=1.0, runtime=200.0),
        _candidate("fast-clean", tests=True, lint=1.0, runtime=10.0),
        _candidate("fast-dirty", tests=True, lint=0.0, runtime=10.0),
    ]
    legacy = select_best(candidates)
    new = select_winner(candidates, profile=None)
    assert new.task_id == legacy.task_id


def test_runner_no_profile_returns_legacy_winner() -> None:
    candidates = [
        _candidate("a", tests=False, lint=1.0, runtime=10.0),
        _candidate("b", tests=True, lint=1.0, runtime=60.0),
        _candidate("c", tests=True, lint=0.5, runtime=10.0),
    ]
    runner = _runner_for(candidates)
    outcome = runner.run(_make_task(), n=3)
    assert outcome.winner.task_id == select_best(candidates).task_id


# ---------------------------------------------------------------------------
# Profile-driven winners
# ---------------------------------------------------------------------------


def test_safety_first_profile_picks_passing_candidate_even_when_slow() -> None:
    safety = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=100.0),
            Criterion("cost", direction="cost", weight=1.0),
            Criterion("latency", direction="cost", weight=1.0),
        )
    )
    candidates = [
        _candidate("safe-slow", tests=True, lint=1.0, runtime=600.0, judge=0.95),
        _candidate("risky-fast", tests=False, lint=1.0, runtime=10.0, judge=0.4),
    ]
    winner = select_winner(candidates, profile=safety)
    assert winner.task_id == "safe-slow"


def test_speed_first_profile_picks_fastest_candidate() -> None:
    speed = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=1.0),
            Criterion("latency", direction="cost", weight=100.0),
        )
    )
    candidates = [
        _candidate("slow", tests=True, lint=1.0, runtime=600.0, judge=0.95),
        _candidate("fast", tests=True, lint=1.0, runtime=5.0, judge=0.95),
    ]
    winner = select_winner(candidates, profile=speed)
    assert winner.task_id == "fast"


def test_different_profiles_produce_different_winners() -> None:
    """Issue acceptance: same candidates + different profile -> different
    winner."""
    candidates = [
        _candidate("safe-expensive", tests=True, lint=1.0, runtime=600.0, judge=1.0),
        _candidate("risky-cheap", tests=False, lint=1.0, runtime=5.0, judge=0.0),
    ]
    safety = build_criterion_profile(["correctness", "latency"], weights=[100.0, 1.0])
    speed = build_criterion_profile(["correctness", "latency"], weights=[1.0, 100.0])
    w_safety = select_winner(candidates, profile=safety)
    w_speed = select_winner(candidates, profile=speed)
    assert w_safety.task_id != w_speed.task_id


# ---------------------------------------------------------------------------
# Edge cases through the public API
# ---------------------------------------------------------------------------


def test_single_candidate_is_noop_under_profile() -> None:
    """Issue acceptance: feature is a no-op when fewer than 2 candidates
    are produced."""
    only = [_candidate("solo", tests=True, lint=1.0, runtime=10.0)]
    profile = build_criterion_profile(["correctness", "latency"])
    assert select_winner(only, profile=profile).task_id == "solo"


def test_select_winner_empty_raises() -> None:
    with pytest.raises(ValueError):
        select_winner([], profile=None)


def test_select_winner_rejects_non_profile_object() -> None:
    candidates = [_candidate("a"), _candidate("b")]
    with pytest.raises(TypeError):
        select_winner(candidates, profile="not-a-profile")  # type: ignore[arg-type]


def test_runner_outcome_winner_id_matches_select_winner() -> None:
    """End-to-end: BestOfNRunner.run -> outcome.winner is the legacy
    winner when no profile is configured at the runner level."""
    candidates = [
        _candidate("a", tests=True, lint=1.0, runtime=60.0),
        _candidate("b", tests=True, lint=0.9, runtime=10.0),
        _candidate("c", tests=False, lint=1.0, runtime=10.0),
    ]
    runner = _runner_for(candidates)
    outcome = runner.run(_make_task(), n=3)
    assert outcome.winner.task_id in {c.task_id for c in candidates}
    # losers + winner == all candidates
    all_ids = {c.task_id for c in outcome.candidates}
    assert all_ids == {c.task_id for c in candidates}


def test_select_winner_determinism_across_invocations() -> None:
    """Issue acceptance: 100 invocations -> identical winner."""
    candidates = [
        _candidate("alpha", tests=True, lint=1.0, runtime=60.0, judge=0.9),
        _candidate("beta", tests=True, lint=0.8, runtime=30.0, judge=0.7),
        _candidate("gamma", tests=False, lint=1.0, runtime=5.0, judge=0.5),
    ]
    profile = build_criterion_profile(["correctness", "latency"])
    winners = {select_winner(candidates, profile=profile).task_id for _ in range(100)}
    assert len(winners) == 1


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _write_artefact(dirpath: Path, task_id: str, payload: dict[str, object]) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / f"{task_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cli_show_renders_recorded_ranking(tmp_path: Path) -> None:
    """``bernstein best-of-n show <id>`` renders the artefact's stored
    ranking when no --rank-criteria is supplied."""
    payload = {
        "method": "topsis",
        "winner": "cand-a",
        "ranking": [
            {"key": "cand-a", "rank": 1, "closeness": 0.812345},
            {"key": "cand-b", "rank": 2, "closeness": 0.421000},
        ],
    }
    _write_artefact(tmp_path, "task-x", payload)
    runner = CliRunner()
    result = runner.invoke(
        best_of_n_group,
        ["show", "task-x", "--artefact-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Winner: cand-a" in result.output
    assert "cand-a" in result.output
    assert "cand-b" in result.output


def test_cli_show_recomputes_with_explicit_criteria(tmp_path: Path) -> None:
    payload = {
        "method": "topsis",
        "candidates": [
            {"task_id": "alpha", "scores": {"correctness": 1.0, "cost": 10.0}},
            {"task_id": "beta", "scores": {"correctness": 0.5, "cost": 1.0}},
        ],
    }
    _write_artefact(tmp_path, "task-y", payload)
    runner = CliRunner()
    result = runner.invoke(
        best_of_n_group,
        [
            "show",
            "task-y",
            "--rank-criteria",
            "correctness,cost",
            "--weights",
            "100,1",
            "--artefact-dir",
            str(tmp_path),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["winner"] == "alpha"


def test_cli_show_missing_artefact_returns_error(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        best_of_n_group,
        ["show", "no-such-task", "--artefact-dir", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "No best-of-N artefact" in result.output


def test_cli_show_rejects_invalid_weights(tmp_path: Path) -> None:
    payload = {
        "candidates": [
            {"task_id": "a", "scores": {"x": 1.0}},
            {"task_id": "b", "scores": {"x": 2.0}},
        ],
    }
    _write_artefact(tmp_path, "task-z", payload)
    runner = CliRunner()
    result = runner.invoke(
        best_of_n_group,
        [
            "show",
            "task-z",
            "--rank-criteria",
            "x",
            "--weights",
            "not-a-number",
            "--artefact-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "Invalid weights" in result.output
