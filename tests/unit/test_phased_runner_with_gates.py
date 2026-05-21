"""End-to-end runner tests for the boundary gate.

These exercise the retry loop, the lineage hook, the hard-fail path on
R005-byte-budget, and the failure-kind on retry exhaustion.  Per-rule
correctness is covered separately in ``test_phase_gates.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bernstein.core.orchestration.phase_pipeline import (
    ArtifactStore,
    Phase,
    PhaseArtifact,
    PhasedRunner,
    PhaseGateFailure,
    PhaseSpec,
)
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus, TaskType


def _make_task(metadata: dict[str, object] | None = None) -> Task:
    return Task(
        id="t-gate-1",
        title="title",
        description="desc",
        role="backend",
        priority=2,
        scope=Scope.MEDIUM,
        complexity=Complexity.MEDIUM,
        status=TaskStatus.OPEN,
        task_type=TaskType.STANDARD,
        metadata=dict(metadata or {}),
    )


def _research_artifact(open_qs: list[str] | None = None) -> PhaseArtifact:
    return PhaseArtifact(
        summary="research summary long enough to satisfy the strict schema",
        decisions=["use existing TaskStore <id:taskstore>"],
        constraints=["python 3.12", "pyright strict"],
        open_questions=open_qs or [],
    )


def _plan_artifact(open_qs: list[str] | None = None) -> PhaseArtifact:
    return PhaseArtifact(
        summary="plan derived from research summary cleanly",
        decisions=["adopt <id:taskstore>", "step1 -> step2"],
        constraints=["python 3.12", "pyright strict"],
        open_questions=open_qs or [],
        extras={"dependencies": ["step1->step2"]},
    )


def _implement_artifact() -> PhaseArtifact:
    return PhaseArtifact(
        summary="implemented changes per plan",
        decisions=["committed"],
        constraints=["python 3.12", "pyright strict"],
        open_questions=[],
        extras={
            "files_changed": ["src/foo.py"],
            "tests_added": ["tests/unit/test_foo.py"],
            "tests_passing": ["tests/unit/test_foo.py::test_smoke"],
        },
    )


# ---------------------------------------------------------------------------
# Happy path - gate passes on every boundary, no retries.
# ---------------------------------------------------------------------------


def test_gate_pass_proceeds_with_zero_retries(tmp_path: Path) -> None:
    def executor(_task: Task, spec: PhaseSpec, _prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            return _plan_artifact()
        return _implement_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        gate_max_retries=1,
    )
    results = runner.run(_make_task())
    assert [r.retry_count for r in results] == [0, 0, 0]
    # Every result should have one or more gate entries (research uses
    # the self-boundary path; plan/implement use the from->to path).
    assert all(r.gate_results for r in results)


# ---------------------------------------------------------------------------
# Single retry - first attempt fails R001, second attempt passes.
# ---------------------------------------------------------------------------


def test_gate_failure_re_fires_failing_phase_with_violation_seed(tmp_path: Path) -> None:
    seen_open_questions: list[list[str]] = []

    def executor(_task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            seen_open_questions.append(list(prior.open_questions) if prior else [])
            # First attempt: leave open_questions populated -> R001 fails.
            # Second attempt: clean payload (the seed open_questions are
            # interpreted by the agent and *resolved*).
            if len(seen_open_questions) == 1:
                return _plan_artifact(open_qs=["unresolved!"])
            return _plan_artifact()
        return _implement_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        gate_max_retries=1,
    )
    results = runner.run(_make_task())

    # Plan was retried once.
    plan_result = next(r for r in results if r.phase is Phase.PLAN)
    assert plan_result.retry_count == 1

    # Second invocation saw the violation seeded into open_questions.
    assert len(seen_open_questions) == 2
    second_seed = seen_open_questions[1]
    assert any("R001-no-open-questions" in q for q in second_seed)


# ---------------------------------------------------------------------------
# Retry exhausted - task fails with PhaseGateFailure.
# ---------------------------------------------------------------------------


def test_second_failure_raises_phase_gate_failure(tmp_path: Path) -> None:
    def executor(_task: Task, spec: PhaseSpec, _prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            # Always leaves an open question -> R001 keeps failing.
            return _plan_artifact(open_qs=["nope"])
        return _implement_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        gate_max_retries=1,
    )
    with pytest.raises(PhaseGateFailure) as excinfo:
        runner.run(_make_task())
    err = excinfo.value
    assert err.phase is Phase.PLAN
    assert err.retry_count == 1
    rule_ids = {f.rule_id for f in err.failures}
    assert "R001-no-open-questions" in rule_ids


# ---------------------------------------------------------------------------
# R005 byte-budget - hard fail bypasses the retry budget.
# ---------------------------------------------------------------------------


def test_byte_budget_hard_fail_raises_immediately(tmp_path: Path) -> None:
    # Schema enforces summary maxLength=8000 and per-item minLength=1
    # but no per-item maxLength on the decisions array.  A handful of
    # huge decision strings trips R005 without violating the schema.
    big_chunk = "x" * 100_000

    def executor(_task: Task, spec: PhaseSpec, _prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return PhaseArtifact(
                summary="research summary long enough to satisfy the strict schema",
                decisions=[big_chunk, big_chunk, big_chunk],
                constraints=[],
                open_questions=[],
            )
        return _plan_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        gate_max_retries=5,  # retries are irrelevant under hard fail
        gate_byte_budget_hard_fail=True,
        # Explicitly use only research and plan so the test stays focused.
        phases=[Phase.RESEARCH, Phase.PLAN],
    )
    with pytest.raises(PhaseGateFailure) as excinfo:
        runner.run(_make_task())
    rule_ids = {f.rule_id for f in excinfo.value.failures}
    assert "R005-byte-budget" in rule_ids
    assert excinfo.value.retry_count == 0


# ---------------------------------------------------------------------------
# Lineage hook receives every boundary's gate results.
# ---------------------------------------------------------------------------


def test_lineage_hook_receives_per_boundary_results(tmp_path: Path) -> None:
    captured: list[tuple[Phase, tuple[Phase, Phase], int]] = []

    def hook(_task: Task, phase: Phase, boundary: tuple[Phase, Phase], results: list[Any]) -> None:
        captured.append((phase, boundary, len(results)))

    def executor(_task: Task, spec: PhaseSpec, _prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            return _plan_artifact()
        return _implement_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        gate_lineage_hook=hook,
    )
    runner.run(_make_task())

    # Three boundaries: research-self, research->plan, plan->implement.
    boundaries = [b for _, b, _ in captured]
    assert (Phase.RESEARCH, Phase.RESEARCH) in boundaries
    assert (Phase.RESEARCH, Phase.PLAN) in boundaries
    assert (Phase.PLAN, Phase.IMPLEMENT) in boundaries
    # Every captured tuple has at least one rule result.
    assert all(count > 0 for _, _, count in captured)


# ---------------------------------------------------------------------------
# Single-phase tasks - gate runner is a no-op.
# ---------------------------------------------------------------------------


def test_single_phase_task_skips_gate_machinery(tmp_path: Path) -> None:
    def executor(_task: Task, spec: PhaseSpec, _prior: PhaseArtifact | None) -> PhaseArtifact:
        del spec
        return _research_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        phases=[Phase.RESEARCH],
        gate_enabled=False,
    )
    results = runner.run(_make_task())
    assert len(results) == 1
    assert results[0].retry_count == 0
    assert results[0].gate_results == []


# ---------------------------------------------------------------------------
# Plan YAML denylist - disables one rule.
# ---------------------------------------------------------------------------


def test_gate_denylist_disables_single_rule(tmp_path: Path) -> None:
    def executor(_task: Task, spec: PhaseSpec, _prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            # Leaves an open question - would normally fail R001.
            return _plan_artifact(open_qs=["intentional"])
        return _implement_artifact()

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path),
        gate_max_retries=0,  # zero retries - failure would raise
        gate_denied=["R001-no-open-questions"],
    )
    # No exception: R001 was suppressed via the denylist.
    runner.run(_make_task())
