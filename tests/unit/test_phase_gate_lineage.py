"""Lineage-emission helper for phase-gate boundary events.

The hook builder writes one ``phase_gate``-tagged record into the run's
WAL per boundary.  The hash chain stays a single chain - we reuse the
existing :class:`LineageWriter` rather than introducing a parallel one.
"""

from __future__ import annotations

from pathlib import Path

from bernstein.core.orchestration.phase_gate_lineage import (
    PHASE_GATE_REGULATORY_CLASS,
    build_phase_gate_record,
    gate_results_summary,
    make_lineage_hook,
)
from bernstein.core.orchestration.phase_gates import GateOutcome, GateResult
from bernstein.core.orchestration.phase_pipeline import Phase
from bernstein.core.persistence.lineage import LineageReader, LineageWriter
from bernstein.core.tasks.models import Complexity, Scope, Task, TaskStatus, TaskType


def _task() -> Task:
    return Task(
        id="t-lineage-1",
        title="t",
        description="d",
        role="backend",
        priority=2,
        scope=Scope.MEDIUM,
        complexity=Complexity.MEDIUM,
        status=TaskStatus.OPEN,
        task_type=TaskType.STANDARD,
        metadata={},
    )


def _result(rule_id: str, outcome: GateOutcome) -> GateResult:
    return GateResult(
        rule_id=rule_id,
        label=rule_id,
        outcome=outcome,
        boundary_from=Phase.RESEARCH,
        boundary_to=Phase.PLAN,
    )


def test_build_record_tags_regulatory_class(tmp_path: Path) -> None:
    fake_artifact = tmp_path / "research.json"
    fake_artifact.write_text('{"summary": "x"}', encoding="utf-8")
    record = build_phase_gate_record(
        task=_task(),
        phase=Phase.PLAN,
        boundary=(Phase.RESEARCH, Phase.PLAN),
        results=[_result("R001-no-open-questions", GateOutcome.PASS)],
        artifact_path=fake_artifact,
    )
    assert record.regulatory_class == PHASE_GATE_REGULATORY_CLASS
    assert record.producer.agent_id == "phase_gate:plan"
    assert record.producer.tick_id == "research->plan"
    # prompt_sha is the stable hash of the rule outcomes.
    assert len(record.prompt_sha) == 64


def test_gate_results_summary_includes_per_rule_outcome() -> None:
    summary = gate_results_summary(
        [
            _result("R001-no-open-questions", GateOutcome.PASS),
            _result("R005-byte-budget", GateOutcome.FAIL),
        ]
    )
    rule_ids = {entry["rule_id"] for entry in summary["rules"]}
    assert rule_ids == {"R001-no-open-questions", "R005-byte-budget"}
    outcomes = {entry["outcome"] for entry in summary["rules"]}
    assert outcomes == {"pass", "fail"}


def test_lineage_hook_writes_one_record_per_call(tmp_path: Path) -> None:
    sdd_dir = tmp_path / ".sdd"
    fake_artifact = tmp_path / "phase.json"
    fake_artifact.write_text("{}", encoding="utf-8")

    writer = LineageWriter.for_run("run-1", sdd_dir)
    hook = make_lineage_hook(
        writer,
        artifact_path_resolver=lambda _t, _p: fake_artifact,
    )
    hook(
        _task(),
        Phase.PLAN,
        (Phase.RESEARCH, Phase.PLAN),
        [_result("R001-no-open-questions", GateOutcome.PASS)],
    )
    hook(
        _task(),
        Phase.IMPLEMENT,
        (Phase.PLAN, Phase.IMPLEMENT),
        [_result("R004-monotonic-constraint-set", GateOutcome.PASS)],
    )

    reader = LineageReader(sdd_dir)
    records = list(reader.iter_records())
    assert len(records) == 2
    classes = {r.regulatory_class for r in records}
    assert classes == {PHASE_GATE_REGULATORY_CLASS}
    actor_ids = {r.producer.agent_id for r in records}
    assert actor_ids == {"phase_gate:plan", "phase_gate:implement"}
