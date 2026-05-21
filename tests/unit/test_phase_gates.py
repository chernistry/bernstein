"""Per-rule unit tests for the mechanical phase-gate runner.

Each of R001..R005 has at least one passing case and one failing case.
The tests exercise the rule callable directly so the runner-level
integration test in ``test_phased_runner_with_gates.py`` can stay
focused on retry-loop semantics.
"""

from __future__ import annotations

from bernstein.core.orchestration.phase_gates import (
    GateOutcome,
    collect_failures,
    evaluate_boundary,
    get_rule,
    list_rules,
    parse_rule_filter,
    violations_to_open_questions,
)
from bernstein.core.orchestration.phase_pipeline import Phase, PhaseArtifact, PhaseSpec


def _spec(phase: Phase = Phase.PLAN, *, max_tokens: int = 30_000) -> PhaseSpec:
    return PhaseSpec(
        phase=phase,
        model="opus",
        effort="high",
        max_tokens=max_tokens,
        output_schema={},
    )


# ---------------------------------------------------------------------------
# R001 - no-open-questions
# ---------------------------------------------------------------------------


def test_r001_passes_with_empty_open_questions() -> None:
    rule = get_rule("R001-no-open-questions")
    assert rule is not None
    prior = PhaseArtifact(summary="x", open_questions=[])
    current = PhaseArtifact(summary="y", open_questions=[])
    result = rule.fn(prior, current, _spec())
    assert result.outcome is GateOutcome.PASS


def test_r001_fails_when_questions_remain() -> None:
    rule = get_rule("R001-no-open-questions")
    assert rule is not None
    prior = PhaseArtifact(summary="x", open_questions=[])
    current = PhaseArtifact(summary="y", open_questions=["q1?", "q2?"])
    result = rule.fn(prior, current, _spec())
    assert result.outcome is GateOutcome.FAIL
    assert "q1?" in result.repair


def test_r001_does_not_apply_to_research_to_research() -> None:
    """Boundary filter excludes research entry - research is allowed to leave Qs."""
    prior = PhaseArtifact(summary="x", open_questions=[])
    current = PhaseArtifact(summary="research", open_questions=["still thinking"])
    results = evaluate_boundary(
        prior=prior,
        current=current,
        boundary=(Phase.RESEARCH, Phase.RESEARCH),
        spec=_spec(Phase.RESEARCH),
    )
    rule_ids = {r.rule_id for r in results}
    assert "R001-no-open-questions" not in rule_ids


# ---------------------------------------------------------------------------
# R002 - decisions-reference-prior
# ---------------------------------------------------------------------------


def test_r002_passes_when_marker_resolves() -> None:
    rule = get_rule("R002-decisions-reference-prior")
    assert rule is not None
    prior = PhaseArtifact(
        summary="r",
        decisions=["pin python <id:py312>"],
        constraints=["pyright strict <id:pyright>"],
    )
    current = PhaseArtifact(
        summary="p",
        decisions=["upgrade <id:py312> to 3.13 later"],
    )
    result = rule.fn(prior, current, _spec())
    assert result.outcome is GateOutcome.PASS


def test_r002_fails_for_unknown_marker() -> None:
    rule = get_rule("R002-decisions-reference-prior")
    assert rule is not None
    prior = PhaseArtifact(summary="r", decisions=[], constraints=[])
    current = PhaseArtifact(summary="p", decisions=["needs <id:phantom>"])
    result = rule.fn(prior, current, _spec())
    assert result.outcome is GateOutcome.FAIL
    assert "phantom" in result.repair


# ---------------------------------------------------------------------------
# R003 - acyclic-decision-graph
# ---------------------------------------------------------------------------


def test_r003_skipped_when_no_edges() -> None:
    rule = get_rule("R003-acyclic-decision-graph")
    assert rule is not None
    current = PhaseArtifact(summary="p", decisions=["plain decision"])
    result = rule.fn(PhaseArtifact(summary="r"), current, _spec())
    assert result.outcome is GateOutcome.SKIPPED


def test_r003_passes_for_acyclic_chain() -> None:
    rule = get_rule("R003-acyclic-decision-graph")
    assert rule is not None
    current = PhaseArtifact(
        summary="p",
        decisions=["a -> b", "b -> c"],
    )
    result = rule.fn(PhaseArtifact(summary="r"), current, _spec())
    assert result.outcome is GateOutcome.PASS


def test_r003_fails_for_cycle() -> None:
    rule = get_rule("R003-acyclic-decision-graph")
    assert rule is not None
    current = PhaseArtifact(
        summary="p",
        decisions=["a -> b", "b -> c", "c -> a"],
    )
    result = rule.fn(PhaseArtifact(summary="r"), current, _spec())
    assert result.outcome is GateOutcome.FAIL
    assert "cycle" in result.repair.lower()


def test_r003_consumes_dependencies_extra() -> None:
    """The plan phase carries an explicit ``dependencies`` edge list."""
    rule = get_rule("R003-acyclic-decision-graph")
    assert rule is not None
    current = PhaseArtifact(
        summary="p",
        decisions=[],
        extras={"dependencies": ["x->y", "y->x"]},
    )
    result = rule.fn(PhaseArtifact(summary="r"), current, _spec())
    assert result.outcome is GateOutcome.FAIL


# ---------------------------------------------------------------------------
# R004 - monotonic-constraint-set
# ---------------------------------------------------------------------------


def test_r004_passes_when_constraints_preserved() -> None:
    rule = get_rule("R004-monotonic-constraint-set")
    assert rule is not None
    prior = PhaseArtifact(summary="p", constraints=["python 3.12", "pyright strict"])
    current = PhaseArtifact(
        summary="i",
        constraints=["python 3.12", "pyright strict", "ruff clean"],
    )
    result = rule.fn(prior, current, _spec())
    assert result.outcome is GateOutcome.PASS


def test_r004_fails_when_constraint_silently_dropped() -> None:
    rule = get_rule("R004-monotonic-constraint-set")
    assert rule is not None
    prior = PhaseArtifact(summary="p", constraints=["python 3.12", "pyright strict"])
    current = PhaseArtifact(summary="i", constraints=["python 3.12"])
    result = rule.fn(prior, current, _spec())
    assert result.outcome is GateOutcome.FAIL
    assert "pyright strict" in result.repair


# ---------------------------------------------------------------------------
# R005 - byte-budget
# ---------------------------------------------------------------------------


def test_r005_passes_within_budget() -> None:
    rule = get_rule("R005-byte-budget")
    assert rule is not None
    current = PhaseArtifact(summary="x" * 100)
    result = rule.fn(PhaseArtifact(summary="r"), current, _spec(max_tokens=10_000))
    assert result.outcome is GateOutcome.PASS


def test_r005_fails_above_budget() -> None:
    rule = get_rule("R005-byte-budget")
    assert rule is not None
    # 100 token budget => 400 byte cap; a 1500-char summary blows it.
    current = PhaseArtifact(summary="x" * 1500)
    result = rule.fn(PhaseArtifact(summary="r"), current, _spec(max_tokens=100))
    assert result.outcome is GateOutcome.FAIL
    assert "exceeds budget" in result.repair


# ---------------------------------------------------------------------------
# Boundary runner / filter parser
# ---------------------------------------------------------------------------


def test_evaluate_boundary_emits_one_result_per_applicable_rule() -> None:
    prior = PhaseArtifact(summary="r", decisions=[], constraints=["c"], open_questions=[])
    current = PhaseArtifact(summary="p", decisions=[], constraints=["c"], open_questions=[])
    results = evaluate_boundary(
        prior=prior,
        current=current,
        boundary=(Phase.RESEARCH, Phase.PLAN),
        spec=_spec(),
    )
    rule_ids = {r.rule_id for r in results}
    assert "R001-no-open-questions" in rule_ids
    assert "R002-decisions-reference-prior" in rule_ids
    assert "R003-acyclic-decision-graph" in rule_ids
    assert "R005-byte-budget" in rule_ids
    # R004 only fires plan->implement.
    assert "R004-monotonic-constraint-set" not in rule_ids


def test_evaluate_boundary_records_boundary_metadata() -> None:
    current = PhaseArtifact(summary="p", open_questions=[])
    results = evaluate_boundary(
        prior=None,
        current=current,
        boundary=(Phase.RESEARCH, Phase.PLAN),
        spec=_spec(),
    )
    for r in results:
        assert r.boundary_from is Phase.RESEARCH
        assert r.boundary_to is Phase.PLAN


def test_parse_rule_filter_splits_allow_and_deny() -> None:
    allowed, denied = parse_rule_filter(["R001-no-open-questions", "-R005-byte-budget"])
    assert allowed == ["R001-no-open-questions"]
    assert denied == ["R005-byte-budget"]


def test_evaluate_boundary_respects_denylist() -> None:
    current = PhaseArtifact(summary="p", open_questions=[])
    results = evaluate_boundary(
        prior=None,
        current=current,
        boundary=(Phase.RESEARCH, Phase.PLAN),
        spec=_spec(),
        denied=["R005-byte-budget"],
    )
    rule_ids = {r.rule_id for r in results}
    assert "R005-byte-budget" not in rule_ids


def test_collect_failures_filters_to_fail_only() -> None:
    current = PhaseArtifact(summary="p", open_questions=["q?"])
    results = evaluate_boundary(
        prior=None,
        current=current,
        boundary=(Phase.RESEARCH, Phase.PLAN),
        spec=_spec(),
    )
    failures = collect_failures(results)
    assert all(f.outcome is GateOutcome.FAIL for f in failures)
    assert any(f.rule_id == "R001-no-open-questions" for f in failures)


def test_violations_to_open_questions_includes_rule_id() -> None:
    current = PhaseArtifact(summary="p", open_questions=["q?"])
    results = evaluate_boundary(
        prior=None,
        current=current,
        boundary=(Phase.RESEARCH, Phase.PLAN),
        spec=_spec(),
    )
    failures = collect_failures(results)
    questions = violations_to_open_questions(failures)
    assert all(q.startswith("R0") for q in questions)
    assert any("R001-no-open-questions" in q for q in questions)


def test_list_rules_contains_all_five_built_ins() -> None:
    ids = {r.rule_id for r in list_rules()}
    assert ids >= {
        "R001-no-open-questions",
        "R002-decisions-reference-prior",
        "R003-acyclic-decision-graph",
        "R004-monotonic-constraint-set",
        "R005-byte-budget",
    }
