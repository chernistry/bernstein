"""Coverage for the phase-pipeline dark paths: gates, retries, lineage hooks.

The base suite covers happy-path execution and parsing; this module pins
down:

* :meth:`PhaseSpec.render_prompt_contract` - fenced JSON contract.
* :meth:`PhaseArtifact.from_json` / :meth:`from_dict` with strict per-phase
  schema validation (success + :class:`PhaseValidationError`).
* :func:`parse_phases` rejecting a non-string entry.
* :meth:`PhasedRunner._seed_with_violations` - both the ``prior is None`` and
  ``prior is not None`` branches.
* :meth:`PhasedRunner.run` boundary-gate retry loop:
  - re-fire on gate failure then succeed,
  - exhaust retries -> :class:`PhaseGateFailure`,
  - R005 byte-budget hard fail at a mid-pipeline boundary,
  - gate lineage hook raising (mid-pipeline and first boundary) is swallowed.
* :func:`is_phased` swallowing a parse error; :func:`task_phases` on a
  non-dict metadata.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bernstein.core.orchestration.phase_pipeline import (
    ArtifactStore,
    Phase,
    PhaseArtifact,
    PhasedRunner,
    PhaseGateFailure,
    PhaseSpec,
    PhaseValidationError,
    is_phased,
    parse_phases,
    task_phases,
)
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus, TaskType


def _task(*, task_id: str = "t-1", metadata: dict[str, object] | None = None) -> Task:
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
        metadata=dict(metadata or {}),
    )


def _research_artifact(open_questions: list[str] | None = None) -> PhaseArtifact:
    return PhaseArtifact(
        summary="codebase reads ~60kb; key modules: orchestration, tasks",
        decisions=["use existing TaskStore", "no new schema"],
        constraints=["python 3.12", "pyright strict"],
        open_questions=open_questions if open_questions is not None else ["batch policy interaction"],
    )


def _plan_artifact(*, prior: PhaseArtifact, open_questions: list[str] | None = None) -> PhaseArtifact:
    return PhaseArtifact(
        summary=f"plan derived from research summary len={len(prior.summary)}",
        decisions=["step 1 add module", "step 2 wire loader"],
        constraints=list(prior.constraints),
        open_questions=open_questions if open_questions is not None else [],
        extras={"dependencies": ["step1->step2"]},
    )


def _implement_artifact(*, prior: PhaseArtifact | None) -> PhaseArtifact:
    return PhaseArtifact(
        summary="implemented from prior plan" if prior is not None else "implemented (no prior plan)",
        decisions=["committed"],
        constraints=list(prior.constraints) if prior is not None else [],
        open_questions=[],
        extras={
            "files_changed": ["src/foo.py"],
            "tests_added": ["tests/unit/test_foo.py"],
            "tests_passing": ["tests/unit/test_foo.py::test_smoke"],
        },
    )


def _good_executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
    if spec.phase is Phase.RESEARCH:
        return _research_artifact()
    if spec.phase is Phase.PLAN:
        assert prior is not None
        return _plan_artifact(prior=prior)
    return _implement_artifact(prior=prior)


# ---------------------------------------------------------------------------
# PhaseSpec.render_prompt_contract
# ---------------------------------------------------------------------------


def test_render_prompt_contract_emits_fenced_json() -> None:
    spec = PhaseSpec.default(Phase.RESEARCH)
    contract = spec.render_prompt_contract()
    assert contract.startswith("```json\n")
    assert contract.endswith("\n```")
    # The body between the fences must be the spec's schema, round-trippable.
    body = contract.removeprefix("```json\n").removesuffix("\n```")
    parsed = json.loads(body)
    assert parsed == spec.output_schema


# ---------------------------------------------------------------------------
# PhaseArtifact strict validation
# ---------------------------------------------------------------------------


def test_from_dict_strict_phase_validation_passes_for_valid_research() -> None:
    art = _research_artifact()
    rebuilt = PhaseArtifact.from_dict(art.to_payload(), phase=Phase.RESEARCH)
    assert rebuilt.summary == art.summary
    assert rebuilt.decisions == art.decisions


def test_from_dict_strict_phase_validation_raises_for_invalid() -> None:
    # 'summary' too short for the research schema's min length.
    bad = {"summary": "x", "decisions": [], "constraints": [], "open_questions": []}
    with pytest.raises(PhaseValidationError):
        PhaseArtifact.from_dict(bad, phase=Phase.RESEARCH)


def test_from_json_strict_phase_validation_raises_for_invalid() -> None:
    bad = json.dumps({"summary": "x", "decisions": [], "constraints": [], "open_questions": []})
    with pytest.raises(PhaseValidationError):
        PhaseArtifact.from_json(bad, phase=Phase.RESEARCH)


def test_from_json_malformed_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        PhaseArtifact.from_json("{not json", phase=None)


# ---------------------------------------------------------------------------
# parse_phases non-string entry
# ---------------------------------------------------------------------------


def test_parse_phases_rejects_non_string_entry() -> None:
    with pytest.raises(ValueError, match="phase entry must be a string"):
        parse_phases(["research", 123])


# ---------------------------------------------------------------------------
# _seed_with_violations
# ---------------------------------------------------------------------------


def test_seed_with_violations_none_prior_creates_synthetic_seed() -> None:
    runner = PhasedRunner(executor=_good_executor)
    seed = runner._seed_with_violations(None, ["fix R001: open questions remain"])
    assert "previous attempt failed mechanical gate" in seed.summary
    assert seed.open_questions == ["fix R001: open questions remain"]


def test_seed_with_violations_appends_to_existing_prior() -> None:
    runner = PhasedRunner(executor=_good_executor)
    prior = PhaseArtifact(
        summary="prior summary",
        decisions=["d1"],
        constraints=["c1"],
        open_questions=["existing q"],
        extras={"dependencies": []},
    )
    seed = runner._seed_with_violations(prior, ["new violation"])
    assert seed.summary == "prior summary"
    assert seed.decisions == ["d1"]
    assert seed.open_questions == ["existing q", "new violation"]
    # Extras are carried forward (copied, not aliased).
    assert seed.extras == {"dependencies": []}


# ---------------------------------------------------------------------------
# Gate retry path
# ---------------------------------------------------------------------------


def test_run_refires_failing_phase_then_succeeds(tmp_path: Any) -> None:
    """A plan artefact that fails R001 once, then passes, must re-fire once."""
    calls: dict[str, int] = {"plan": 0}

    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            calls["plan"] += 1
            # First attempt leaves an open question (R001 FAIL); retry clears it.
            if calls["plan"] == 1:
                return _plan_artifact(prior=prior, open_questions=["unresolved!"])
            return _plan_artifact(prior=prior, open_questions=[])
        return _implement_artifact(prior=prior)

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        gate_max_retries=1,
    )
    results = runner.run(_task())
    plan_result = next(r for r in results if r.phase is Phase.PLAN)
    assert plan_result.retry_count == 1
    assert calls["plan"] == 2  # initial + one re-fire


def test_run_raises_phase_gate_failure_when_retries_exhausted(tmp_path: Any) -> None:
    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        if spec.phase is Phase.RESEARCH:
            return _research_artifact()
        if spec.phase is Phase.PLAN:
            # Always fails R001 (open question never clears).
            return _plan_artifact(prior=prior, open_questions=["never resolved"])
        return _implement_artifact(prior=prior)

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        gate_max_retries=1,
    )
    with pytest.raises(PhaseGateFailure) as exc:
        runner.run(_task())
    assert exc.value.phase is Phase.PLAN
    assert exc.value.boundary == (Phase.RESEARCH, Phase.PLAN)
    assert any(f.rule_id == "R001-no-open-questions" for f in exc.value.failures)


def test_run_byte_budget_hard_fail_at_first_boundary(tmp_path: Any) -> None:
    # Research artefact whose serialised size blows past the R005 soft cap.
    huge_summary = "x" * 5000

    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        return PhaseArtifact(
            summary=huge_summary,
            decisions=["d"],
            constraints=["c"],
            open_questions=[],
        )

    # Tiny budget on the research phase forces R005 to fail at the very first
    # (research, research) boundary.
    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        phases=[Phase.RESEARCH],
        gate_byte_budget_hard_fail=True,
    )
    # Patch the research spec's max_tokens down so the artefact is over-budget.
    original_default = PhaseSpec.default

    def _tiny_default(phase: Phase) -> PhaseSpec:
        base = original_default(phase)
        return PhaseSpec(
            phase=base.phase,
            model=base.model,
            effort=base.effort,
            max_tokens=1,  # 1 token cap => any real artefact busts the budget
            output_schema=base.output_schema,
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(PhaseSpec, "default", staticmethod(_tiny_default))
        with pytest.raises(PhaseGateFailure) as exc:
            runner.run(_task())
    assert any(f.rule_id == "R005-byte-budget" for f in exc.value.failures)


def test_run_lineage_hook_exception_is_swallowed_first_boundary(tmp_path: Any) -> None:
    hook_calls: list[Any] = []

    def raising_hook(task: Task, phase: Phase, boundary: Any, results: list[Any]) -> None:
        hook_calls.append((phase, boundary))
        raise RuntimeError("lineage sink down")

    runner = PhasedRunner(
        executor=_good_executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        phases=[Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT],
        gate_lineage_hook=raising_hook,
    )
    # The run must complete despite the hook raising on every boundary.
    results = runner.run(_task())
    assert [r.phase for r in results] == [Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT]
    # The hook fired at the first (research,research) boundary and each
    # subsequent boundary.
    assert (Phase.RESEARCH, (Phase.RESEARCH, Phase.RESEARCH)) in hook_calls


def test_run_lineage_hook_receives_gate_results(tmp_path: Any) -> None:
    collected: list[tuple[Phase, list[Any]]] = []

    def collecting_hook(task: Task, phase: Phase, boundary: Any, results: list[Any]) -> None:
        collected.append((phase, results))

    runner = PhasedRunner(
        executor=_good_executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        gate_lineage_hook=collecting_hook,
    )
    runner.run(_task())
    phases_seen = {phase for phase, _ in collected}
    # Lineage hook fires for every boundary including the synthetic first one.
    assert Phase.RESEARCH in phases_seen
    assert Phase.PLAN in phases_seen
    assert Phase.IMPLEMENT in phases_seen


# ---------------------------------------------------------------------------
# _execute_one schema validation
# ---------------------------------------------------------------------------


def test_run_raises_validation_error_for_schema_violating_artifact(tmp_path: Any) -> None:
    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        # Research summary far too short for the schema's min length.
        return PhaseArtifact(summary="x", decisions=[], constraints=[], open_questions=[])

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        phases=[Phase.RESEARCH],
    )
    with pytest.raises(PhaseValidationError):
        runner.run(_task())


def test_run_raises_type_error_for_non_artifact_return(tmp_path: Any) -> None:
    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> Any:
        return {"summary": "not an artifact"}

    runner = PhasedRunner(
        executor=executor,
        store=ArtifactStore(root=tmp_path / "artifacts"),
        phases=[Phase.RESEARCH],
    )
    with pytest.raises(TypeError, match="expected PhaseArtifact"):
        runner.run(_task())


# ---------------------------------------------------------------------------
# is_phased / task_phases edge cases
# ---------------------------------------------------------------------------


def test_is_phased_swallows_parse_error_returns_false() -> None:
    # metadata['phases'] is truthy but contains an invalid phase name, so
    # parse_phases raises ValueError which is swallowed -> False.
    task = _task(metadata={"phases": ["research", "not-a-real-phase"]})
    assert is_phased(task) is False


def test_is_phased_false_for_empty_phases_list() -> None:
    task = _task(metadata={"phases": []})
    assert is_phased(task) is False


def test_task_phases_non_dict_metadata_returns_empty() -> None:
    task = _task()
    object.__setattr__(task, "metadata", None)
    assert task_phases(task) == []


def test_task_phases_parses_declared_phases() -> None:
    task = _task(metadata={"phases": ["research", "plan"]})
    assert task_phases(task) == [Phase.RESEARCH, Phase.PLAN]
