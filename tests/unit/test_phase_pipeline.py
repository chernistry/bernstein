"""Tests for the discrete-phase-separation pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from bernstein.core.plan_loader import load_plan_from_yaml

from bernstein.core.orchestration.phase_pipeline import (
    ArtifactStore,
    Phase,
    PhaseArtifact,
    PhasedRunner,
    PhaseSpec,
    default_phases,
    is_phased,
    parse_phases,
    route_for_phase,
    task_phases,
)
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus, TaskType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    task_id: str = "t-1",
    model: str | None = None,
    effort: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Task:
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
        model=model,
        effort=effort,
        metadata=dict(metadata or {}),
    )


def _stub_executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
    """Deterministic executor that fabricates an artefact per phase.

    For RESEARCH it pretends to have read 60 KB of source - the resulting
    artefact's serialised form is what the next phase will see, so the
    ratio between ``len(serialised)`` and 60 KB exercises the
    "next phase input < 10% of previous output" claim.
    """
    if spec.phase is Phase.RESEARCH:
        return PhaseArtifact(
            summary="codebase reads ~60kb; key modules: orchestration, tasks",
            decisions=["use existing TaskStore", "no new schema"],
            constraints=["python 3.12", "pyright strict"],
            open_questions=["batch policy interaction"],
        )
    if spec.phase is Phase.PLAN:
        assert prior is not None
        return PhaseArtifact(
            summary=f"plan derived from research summary len={len(prior.summary)}",
            decisions=["step 1 add module", "step 2 wire loader"],
            constraints=list(prior.constraints),
            open_questions=[],
            extras={"dependencies": ["step1->step2"]},
        )
    return PhaseArtifact(
        summary="implemented from prior plan" if prior is not None else "implemented (no prior plan)",
        decisions=["committed"],
        # Carry constraints forward so R004-monotonic-constraint-set passes
        # at the plan->implement boundary (implement is forbidden from
        # silently dropping plan-level constraints).
        constraints=list(prior.constraints) if prior is not None else [],
        open_questions=[],
        extras={
            "files_changed": ["src/foo.py"],
            "tests_added": ["tests/unit/test_foo.py"],
            "tests_passing": ["tests/unit/test_foo.py::test_smoke"],
        },
    )


# ---------------------------------------------------------------------------
# Phase enum and parser
# ---------------------------------------------------------------------------


def test_default_phases_is_research_plan_implement() -> None:
    assert default_phases() == [Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT]


def test_parse_phases_accepts_canonical_list() -> None:
    out = parse_phases(["research", "plan", "implement"])
    assert out == [Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT]


def test_parse_phases_returns_empty_for_none() -> None:
    assert parse_phases(None) == []


def test_parse_phases_rejects_unknown_phase() -> None:
    with pytest.raises(ValueError, match="unknown phase"):
        parse_phases(["research", "shenanigans"])


def test_parse_phases_rejects_non_list() -> None:
    with pytest.raises(ValueError):
        parse_phases("research")


# ---------------------------------------------------------------------------
# PhaseArtifact (de)serialisation
# ---------------------------------------------------------------------------


def test_artifact_round_trip() -> None:
    art = PhaseArtifact(
        summary="x",
        decisions=["a", "b"],
        constraints=["c"],
        open_questions=["q?"],
    )
    restored = PhaseArtifact.from_json(art.to_json())
    assert restored == art


def test_artifact_rejects_missing_keys() -> None:
    with pytest.raises(ValueError, match="missing required key"):
        PhaseArtifact.from_dict({"summary": "x", "decisions": [], "constraints": []})


def test_artifact_rejects_wrong_types() -> None:
    with pytest.raises(ValueError):
        PhaseArtifact.from_dict({"summary": 1, "decisions": [], "constraints": [], "open_questions": []})


def test_artifact_rejects_non_str_list() -> None:
    with pytest.raises(ValueError):
        PhaseArtifact.from_dict({"summary": "x", "decisions": [1, 2], "constraints": [], "open_questions": []})


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_route_research_picks_high_reasoning_model() -> None:
    model, effort = route_for_phase(Phase.RESEARCH)
    assert model == "opus"
    assert effort == "high"


def test_route_implement_picks_cheaper_model() -> None:
    model, effort = route_for_phase(Phase.IMPLEMENT)
    assert model == "sonnet"
    assert effort == "normal"


def test_route_respects_task_overrides() -> None:
    model, effort = route_for_phase(Phase.RESEARCH, task_model="haiku", task_effort="low")
    assert model == "haiku"
    assert effort == "low"


# ---------------------------------------------------------------------------
# ArtifactStore
# ---------------------------------------------------------------------------


def test_artifact_store_write_then_read(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / "phase_artifacts")
    art = PhaseArtifact(
        summary="research summary long enough to satisfy schema",
        decisions=[],
        constraints=[],
        open_questions=[],
    )
    path = store.write("task-42", Phase.RESEARCH, art)
    assert path.exists()
    assert path.parent.name == "task-42"
    assert path.name == "research.json"

    restored = store.read("task-42", Phase.RESEARCH)
    assert restored == art


def test_artifact_store_gc_removes_task_dir(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / "phase_artifacts")
    research_art = PhaseArtifact(
        summary="research summary long enough to satisfy schema",
        decisions=[],
        constraints=[],
        open_questions=[],
    )
    plan_art = PhaseArtifact(
        summary="plan summary long enough to satisfy schema",
        decisions=[],
        constraints=[],
        open_questions=[],
        extras={"dependencies": ["a->b"]},
    )
    store.write("doomed", Phase.RESEARCH, research_art)
    store.write("doomed", Phase.PLAN, plan_art)
    assert store.gc_task("doomed") is True
    assert store.read("doomed", Phase.RESEARCH) is None
    assert store.gc_task("doomed") is False


# ---------------------------------------------------------------------------
# PhasedRunner - the meat of the pattern
# ---------------------------------------------------------------------------


def test_runner_executes_phases_in_order(tmp_path: Path) -> None:
    seen: list[Phase] = []

    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        seen.append(spec.phase)
        return _stub_executor(task, spec, prior)

    runner = PhasedRunner(executor=executor, store=ArtifactStore(root=tmp_path))
    results = runner.run(_make_task())

    assert seen == [Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT]
    assert [r.phase for r in results] == seen
    assert all(r.artifact_path.exists() for r in results)


def test_runner_picks_high_reasoning_model_for_research_only(tmp_path: Path) -> None:
    seen: dict[Phase, PhaseSpec] = {}

    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        seen[spec.phase] = spec
        return _stub_executor(task, spec, prior)

    PhasedRunner(executor=executor, store=ArtifactStore(root=tmp_path)).run(_make_task())

    assert seen[Phase.RESEARCH].model == "opus"
    assert seen[Phase.PLAN].model == "opus"
    assert seen[Phase.IMPLEMENT].model == "sonnet"


def test_runner_handoff_is_strictly_smaller_than_simulated_research(tmp_path: Path) -> None:
    """The implement phase must see <10% of the simulated research bulk.

    The stub research artefact is intentionally terse - the whole point of
    distillation is that 60 KB of exploration compresses to a few hundred
    bytes of conclusions.
    """
    SIMULATED_RESEARCH_BYTES = 60_000

    runner = PhasedRunner(executor=_stub_executor, store=ArtifactStore(root=tmp_path))
    results = runner.run(_make_task())

    research_result = results[0]
    plan_result = results[1]
    implement_result = results[2]

    # The plan phase only ever sees the research artefact, not the raw 60 KB.
    assert plan_result.input_bytes == research_result.output_bytes
    assert plan_result.input_bytes < SIMULATED_RESEARCH_BYTES * 0.10

    # The implement phase only ever sees the plan artefact.
    assert implement_result.input_bytes == plan_result.output_bytes
    assert implement_result.input_bytes < SIMULATED_RESEARCH_BYTES * 0.10


def test_runner_implement_prompt_is_only_distilled_json(tmp_path: Path) -> None:
    """Verify implement phase receives PhaseArtifact, never raw transcript."""
    captured: dict[Phase, PhaseArtifact | None] = {}

    def executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> PhaseArtifact:
        captured[spec.phase] = prior
        return _stub_executor(task, spec, prior)

    PhasedRunner(executor=executor, store=ArtifactStore(root=tmp_path)).run(_make_task())

    assert captured[Phase.RESEARCH] is None
    plan_input = captured[Phase.PLAN]
    impl_input = captured[Phase.IMPLEMENT]
    assert isinstance(plan_input, PhaseArtifact)
    assert isinstance(impl_input, PhaseArtifact)
    # The implement phase's input is the plan artefact - small, structured.
    assert "decisions" in impl_input.to_json()
    assert len(impl_input.to_json()) < 2_000


def test_runner_rejects_non_artifact_executor(tmp_path: Path) -> None:
    def bad_executor(task: Task, spec: PhaseSpec, prior: PhaseArtifact | None) -> object:
        return {"summary": "not a PhaseArtifact"}

    runner = PhasedRunner(executor=bad_executor, store=ArtifactStore(root=tmp_path))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="expected PhaseArtifact"):
        runner.run(_make_task())


# ---------------------------------------------------------------------------
# Task metadata helpers
# ---------------------------------------------------------------------------


def test_is_phased_true_when_metadata_set() -> None:
    task = _make_task(metadata={"phases": ["research", "plan", "implement"]})
    assert is_phased(task) is True
    assert task_phases(task) == [Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT]


def test_is_phased_false_for_legacy_task() -> None:
    task = _make_task()
    assert is_phased(task) is False
    assert task_phases(task) == []


def test_is_phased_false_for_invalid_metadata() -> None:
    task = _make_task(metadata={"phases": "not-a-list"})
    assert is_phased(task) is False


# ---------------------------------------------------------------------------
# Plan-file parsing (back-compat + new vocabulary)
# ---------------------------------------------------------------------------


def test_plan_loader_back_compat_no_phases(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(
        yaml.dump(
            {
                "name": "legacy",
                "stages": [
                    {
                        "name": "s",
                        "steps": [{"goal": "do thing", "role": "backend"}],
                    }
                ],
            }
        )
    )
    tasks = load_plan_from_yaml(plan_file)
    assert len(tasks) == 1
    assert is_phased(tasks[0]) is False


def test_plan_loader_parses_phases_field(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(
        yaml.dump(
            {
                "name": "phased",
                "stages": [
                    {
                        "name": "s",
                        "steps": [
                            {
                                "goal": "build feature",
                                "role": "backend",
                                "phases": ["research", "plan", "implement"],
                            }
                        ],
                    }
                ],
            }
        )
    )
    tasks = load_plan_from_yaml(plan_file)
    assert len(tasks) == 1
    assert is_phased(tasks[0]) is True
    assert task_phases(tasks[0]) == [Phase.RESEARCH, Phase.PLAN, Phase.IMPLEMENT]


def test_plan_loader_rejects_invalid_phase_name(tmp_path: Path) -> None:
    from bernstein.core.plan_loader import PlanLoadError

    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(
        yaml.dump(
            {
                "name": "bad",
                "stages": [
                    {
                        "name": "s",
                        "steps": [
                            {
                                "goal": "x",
                                "role": "backend",
                                "phases": ["nonsense"],
                            }
                        ],
                    }
                ],
            }
        )
    )
    with pytest.raises(PlanLoadError, match="phases"):
        load_plan_from_yaml(plan_file)
