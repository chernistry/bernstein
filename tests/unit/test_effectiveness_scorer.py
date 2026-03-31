"""Unit tests for effectiveness scoring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bernstein.core.agent_log_aggregator import AgentLogSummary
from bernstein.core.effectiveness import EffectivenessScore, EffectivenessScorer
from bernstein.core.models import AgentSession, ModelConfig, TaskStatus


def _session(session_id: str, *, model: str = "sonnet", effort: str = "high") -> AgentSession:
    return AgentSession(
        id=session_id,
        role="backend",
        task_ids=["T-1"],
        status="dead",
        spawn_ts=100.0,
        heartbeat_ts=340.0,
        model_config=ModelConfig(model, effort),
    )


def _log_summary() -> AgentLogSummary:
    return AgentLogSummary(
        session_id="A-1",
        total_lines=2,
        events=[],
        error_count=0,
        warning_count=0,
        files_modified=["src/app.py"],
        tests_run=True,
        tests_passed=True,
        test_summary="2 passed",
        rate_limit_hits=0,
        compile_errors=0,
        tool_failures=0,
        first_meaningful_action_line=1,
        last_activity_line=2,
        dominant_failure_category=None,
    )


def _score(
    *,
    session_id: str,
    task_id: str,
    total: int,
    role: str = "backend",
    model: str = "sonnet",
    effort: str = "high",
) -> EffectivenessScore:
    return EffectivenessScore(
        session_id=session_id,
        task_id=task_id,
        role=role,
        model=model,
        effort=effort,
        time_score=90,
        quality_score=90,
        efficiency_score=90,
        retry_score=100,
        completion_score=100,
        total=total,
        grade="A" if total >= 90 else "B",
        wall_time_s=120.0,
        estimated_time_s=300.0,
        tokens_used=400,
        retry_count=0,
        fix_count=0,
        gate_pass_rate=1.0,
    )


def test_score_perfect_session(tmp_path: Path, make_task: Any) -> None:
    task = make_task(id="T-1", status=TaskStatus.DONE)
    task.estimated_minutes = 5
    task.result_summary = "Done."
    score = EffectivenessScorer(tmp_path).score(
        _session("A-1"),
        task,
        SimpleNamespace(passed=True, gate_results=[SimpleNamespace(passed=True)]),
        _log_summary(),
    )

    assert score.total >= 90
    assert score.grade == "A"


def test_score_slow_session(tmp_path: Path, make_task: Any) -> None:
    task = make_task(id="T-1", status=TaskStatus.DONE)
    task.estimated_minutes = 1
    task.result_summary = "Done."
    session = _session("A-2")
    session.spawn_ts = 100.0
    session.heartbeat_ts = 280.0

    score = EffectivenessScorer(tmp_path).score(
        session,
        task,
        SimpleNamespace(passed=True, gate_results=[SimpleNamespace(passed=True)]),
        _log_summary(),
    )

    assert score.time_score < 60
    assert 60 <= score.total <= 85


def test_score_failed_gates(tmp_path: Path, make_task: Any) -> None:
    task = make_task(id="T-1", status=TaskStatus.DONE)
    task.estimated_minutes = 5
    task.result_summary = "Done."
    score = EffectivenessScorer(tmp_path).score(
        _session("A-3"),
        task,
        SimpleNamespace(passed=False, gate_results=[SimpleNamespace(passed=False)]),
        _log_summary(),
    )

    assert score.quality_score == 0
    assert score.total < 70


def test_score_retry_penalty(tmp_path: Path, make_task: Any) -> None:
    task = make_task(id="T-1", title="[RETRY 2] Fix parser", status=TaskStatus.DONE)
    task.estimated_minutes = 5
    task.result_summary = "Done."

    score = EffectivenessScorer(tmp_path).score(
        _session("A-4"),
        task,
        SimpleNamespace(passed=True, gate_results=[SimpleNamespace(passed=True)]),
        _log_summary(),
    )

    assert score.retry_score == 50


def test_record_writes_jsonl(tmp_path: Path) -> None:
    scorer = EffectivenessScorer(tmp_path)
    scorer.record(_score(session_id="A-1", task_id="T-1", total=91))

    path = tmp_path / ".sdd" / "metrics" / "effectiveness.jsonl"
    assert path.exists()
    assert '"session_id": "A-1"' in path.read_text(encoding="utf-8")


def test_best_config_for_role(tmp_path: Path) -> None:
    scorer = EffectivenessScorer(tmp_path)
    for idx in range(10):
        scorer.record(_score(session_id=f"opus-{idx}", task_id=f"T-opus-{idx}", total=85, model="opus", effort="max"))
        scorer.record(
            _score(session_id=f"sonnet-{idx}", task_id=f"T-sonnet-{idx}", total=65, model="sonnet", effort="high")
        )

    assert scorer.best_config_for_role("backend") == ("opus", "max")


def test_best_config_insufficient_data(tmp_path: Path) -> None:
    scorer = EffectivenessScorer(tmp_path)
    for idx in range(3):
        scorer.record(_score(session_id=f"A-{idx}", task_id=f"T-{idx}", total=80))

    assert scorer.best_config_for_role("backend") is None


def test_trends(tmp_path: Path) -> None:
    scorer = EffectivenessScorer(tmp_path)
    for idx in range(20):
        scorer.record(_score(session_id=f"A-{idx}", task_id=f"T-{idx}", total=60 + idx))

    assert scorer.trends()["backend"] == "improving"
