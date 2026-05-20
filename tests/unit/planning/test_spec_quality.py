"""Unit tests for the spec-quality checklist gate (issue #1631)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.planning.spec_quality import (
    DEFAULT_MAX_AUTO_FIX_ITERATIONS,
    ChecklistReport,
    Rule,
    RuleResult,
    SpecQualityUnresolvedError,
    auto_fix_loop,
    default_rules,
    evaluate,
    refuse_to_advance,
    render_report,
)

# ---------------------------------------------------------------------------
# Fixture text
# ---------------------------------------------------------------------------

CLEAN_SPEC = """# Add cohort export

## Acceptance criteria

- New endpoint `/cohorts/export` returns CSV.
- Tested via `tests/unit/api/test_cohort_export.py`.

## Out of scope

- Anything outside cohort export.

## Tested via

- pytest tests/unit/api/test_cohort_export.py
"""

DIRTY_SPEC_MISSING_AC = """# Add cohort export

## Out of scope

- Anything outside cohort export.

## Tested via

- pytest tests/unit/api/test_cohort_export.py
"""

DIRTY_SPEC_TODO = """# Spec

## Acceptance criteria
- TODO: figure this out.

## Out of scope
- nothing

## Tested via
- pytest -k something
"""

DIRTY_SPEC_PLACEHOLDER = """# Spec

## Acceptance criteria
- Replace <FEATURE NAME> with something.

## Out of scope
- nothing

## Tested via
- pytest
"""


# ---------------------------------------------------------------------------
# Individual rule pass/fail
# ---------------------------------------------------------------------------


def test_default_rules_pass_on_clean_spec() -> None:
    report = evaluate(CLEAN_SPEC)
    assert report.passed
    assert report.failure_count == 0
    assert {r.rule_id for r in report.results} == {r.rule_id for r in default_rules()}


def test_acceptance_criteria_missing_fails() -> None:
    report = evaluate(DIRTY_SPEC_MISSING_AC)
    assert not report.passed
    failures = {f.rule_id for f in report.required_failures}
    assert "acceptance_criteria_present" in failures


def test_out_of_scope_missing_fails() -> None:
    spec = "# X\n\n## Acceptance criteria\n- a\n\n## Tested via\n- pytest\n"
    report = evaluate(spec)
    assert not report.passed
    assert any(f.rule_id == "out_of_scope_present" for f in report.required_failures)


def test_tested_via_missing_fails() -> None:
    spec = "# X\n\n## Acceptance criteria\n- a\n\n## Out of scope\n- none\n"
    report = evaluate(spec)
    assert not report.passed
    assert any(f.rule_id == "tested_via_present" for f in report.required_failures)


def test_todo_markers_fail() -> None:
    report = evaluate(DIRTY_SPEC_TODO)
    assert not report.passed
    assert any(f.rule_id == "no_todo_markers" for f in report.required_failures)


def test_placeholder_tokens_fail() -> None:
    report = evaluate(DIRTY_SPEC_PLACEHOLDER)
    assert not report.passed
    assert any(f.rule_id == "no_placeholder_tokens" for f in report.required_failures)


def test_ref_paths_exist_fails_when_path_missing(tmp_path: Path) -> None:
    spec = (
        "# X\n\n## Acceptance criteria\n- a\n\n## Out of scope\n- none\n\n"
        "## Tested via\n- pytest\n\n"
        "References `src/bernstein/does_not_exist/missing.py` here.\n"
    )
    report = evaluate(spec, workspace_root=tmp_path)
    assert not report.passed
    assert any(f.rule_id == "ref_paths_exist" for f in report.required_failures)


def test_ref_paths_exist_passes_when_path_present(tmp_path: Path) -> None:
    (tmp_path / "src" / "bernstein").mkdir(parents=True)
    (tmp_path / "src" / "bernstein" / "real.py").write_text("x = 1\n")
    spec = (
        "# X\n\n## Acceptance criteria\n- a\n\n## Out of scope\n- none\n\n"
        "## Tested via\n- pytest\n\n"
        "References `src/bernstein/real.py` here.\n"
    )
    report = evaluate(spec, workspace_root=tmp_path)
    assert report.passed


def test_ref_paths_skipped_when_no_workspace() -> None:
    spec = CLEAN_SPEC + "\nReferences `src/bernstein/anything.py` here.\n"
    report = evaluate(spec, workspace_root=None)
    # Without a workspace root the rule is informational; gate still passes.
    assert report.passed


def test_evaluate_from_path(tmp_path: Path) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(CLEAN_SPEC)
    report = evaluate(spec_file)
    assert report.passed
    assert report.spec_path == spec_file


# ---------------------------------------------------------------------------
# Rule plumbing
# ---------------------------------------------------------------------------


def test_rule_swallows_exceptions() -> None:
    def explode(_text: str, _root: Path | None) -> RuleResult:
        raise RuntimeError("boom")

    rule = Rule(rule_id="explodes", description="raises", check=explode)
    result = rule.evaluate("anything", None)
    assert not result.passed
    assert "RuntimeError" in result.message


def test_optional_rules_do_not_block_advancement() -> None:
    def always_fail(_text: str, _root: Path | None) -> RuleResult:
        return RuleResult(rule_id="optional_fail", passed=False, message="nope")

    optional = Rule(
        rule_id="optional_fail",
        description="never passes",
        check=always_fail,
        required=False,
    )
    report = evaluate(CLEAN_SPEC, rules=[*default_rules(), optional])
    assert report.passed
    assert report.failure_count == 1


def test_render_report_includes_failed_hints() -> None:
    report = evaluate(DIRTY_SPEC_MISSING_AC)
    rendered = render_report(report)
    assert "acceptance_criteria_present" in rendered
    assert "hint:" in rendered


# ---------------------------------------------------------------------------
# Auto-fix loop
# ---------------------------------------------------------------------------


def test_auto_fix_loop_converges() -> None:
    """A fixer that patches every flagged rule must converge inside the budget."""
    calls = {"n": 0}

    def fixer(report: ChecklistReport) -> str:
        calls["n"] += 1
        # Add whatever's missing on each pass.
        text = "# X\n"
        text += "\n## Acceptance criteria\n- one\n"
        text += "\n## Out of scope\n- none\n"
        text += "\n## Tested via\n- pytest\n"
        return text

    starting = "# X\n"
    final = auto_fix_loop(starting, autofix=fixer, max_iterations=3)
    assert final.passed
    assert final.iteration == 1
    assert calls["n"] == 1


def test_auto_fix_loop_returns_first_eval_when_already_passing() -> None:
    final = auto_fix_loop(CLEAN_SPEC, autofix=lambda _r: pytest.fail("should not be called"))
    assert final.passed
    assert final.iteration == 0


def test_auto_fix_loop_respects_zero_budget() -> None:
    final = auto_fix_loop(DIRTY_SPEC_MISSING_AC, autofix=lambda _r: CLEAN_SPEC, max_iterations=0)
    assert not final.passed
    assert final.iteration == 0


def test_auto_fix_loop_negative_budget_raises() -> None:
    with pytest.raises(ValueError):
        auto_fix_loop(CLEAN_SPEC, max_iterations=-1)


def test_auto_fix_loop_stops_when_fixer_returns_none() -> None:
    final = auto_fix_loop(DIRTY_SPEC_MISSING_AC, autofix=lambda _r: None, max_iterations=3)
    assert not final.passed


# ---------------------------------------------------------------------------
# Refuse-to-advance gate
# ---------------------------------------------------------------------------


def test_refuse_to_advance_blocks_after_max_iterations() -> None:
    """A no-op fixer must exhaust the budget and raise."""
    calls = {"n": 0}

    def noop(_report: ChecklistReport) -> str:
        calls["n"] += 1
        return DIRTY_SPEC_MISSING_AC

    with pytest.raises(SpecQualityUnresolvedError) as excinfo:
        refuse_to_advance(
            DIRTY_SPEC_MISSING_AC,
            autofix=noop,
            max_iterations=DEFAULT_MAX_AUTO_FIX_ITERATIONS,
        )
    assert calls["n"] == DEFAULT_MAX_AUTO_FIX_ITERATIONS
    assert excinfo.value.report.iteration == DEFAULT_MAX_AUTO_FIX_ITERATIONS
    assert not excinfo.value.report.passed


def test_refuse_to_advance_passes_clean_spec() -> None:
    report = refuse_to_advance(CLEAN_SPEC)
    assert report.passed


def test_refuse_to_advance_passes_after_autofix() -> None:
    def fix(_r: ChecklistReport) -> str:
        return CLEAN_SPEC

    report = refuse_to_advance(DIRTY_SPEC_MISSING_AC, autofix=fix, max_iterations=3)
    assert report.passed
    assert report.iteration == 1


def test_refuse_to_advance_blocks_without_autofix() -> None:
    """No fixer wired -> single eval -> raises immediately on failure."""
    with pytest.raises(SpecQualityUnresolvedError):
        refuse_to_advance(DIRTY_SPEC_MISSING_AC, max_iterations=3)
