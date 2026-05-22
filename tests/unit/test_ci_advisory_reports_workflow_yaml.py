"""Structural assertions for advisory CI reports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict, cast

import yaml


class WorkflowJob(TypedDict, total=False):
    name: object
    needs: object
    steps: list[object]


class WorkflowFile(TypedDict, total=False):
    jobs: dict[str, WorkflowJob]


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

ADVISORY_JOBS = {
    "typecheck": "Type check report",
    "mutmut-diff": "Mutation report (diff-only)",
    "diff-coverage": "Diff coverage report",
}


def _load() -> WorkflowFile:
    return cast("WorkflowFile", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _jobs() -> dict[str, WorkflowJob]:
    jobs = _load().get("jobs", {})
    assert isinstance(jobs, dict)
    return jobs


def _job(name: str) -> WorkflowJob:
    job = _jobs().get(name)
    assert isinstance(job, dict), f"expected job {name!r}"
    return job


def _needs(job_name: str) -> list[str]:
    needs = _job(job_name).get("needs", [])
    assert isinstance(needs, list), f"{job_name}.needs must be a list"
    return [need for need in cast("list[object]", needs) if isinstance(need, str)]


def _run_blocks(job_name: str) -> list[str]:
    steps = _job(job_name).get("steps", [])
    assert isinstance(steps, list)
    runs: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_map = cast("dict[str, object]", step)
        run = step_map.get("run")
        if isinstance(run, str):
            runs.append(run)
    return runs


def _ci_gate_rollup_script() -> str:
    run = next((body for body in _run_blocks("ci-gate") if "results.json" in body and "plan.json" in body), "")
    assert run, "ci-gate roll-up step is missing"
    match = re.search(r"<<'PY'\n(.*?)\n\s*PY\b", run, re.DOTALL)
    assert match is not None, "ci-gate roll-up must keep its Python heredoc"
    return match.group(1)


def test_advisory_jobs_are_reports_not_gates() -> None:
    """Jobs that mask tool failures must be labelled as reports."""
    jobs = _jobs()
    for job_name, expected_name in ADVISORY_JOBS.items():
        assert jobs[job_name].get("name") == expected_name


def test_advisory_jobs_are_not_ci_gate_inputs() -> None:
    """The aggregate required gate must only depend on enforced jobs."""
    gate_needs = set(_needs("ci-gate"))

    assert gate_needs.isdisjoint(ADVISORY_JOBS)


def test_ci_gate_rollup_does_not_treat_advisory_reports_as_required() -> None:
    """The roll-up allow-lists should not mention jobs outside ci-gate needs."""
    rollup = _ci_gate_rollup_script()

    assert '"typecheck"' not in rollup
    assert '"mutmut-diff"' not in rollup
    assert '"diff-coverage"' not in rollup


def test_mutation_report_comment_matches_advisory_behavior() -> None:
    """Mutation testing is advisory until its command is allowed to fail the job."""
    text = WORKFLOW.read_text(encoding="utf-8")
    mutation_block = text.split("  mutmut-diff:", 1)[1].split("  diff-coverage:", 1)[0]

    assert "Mutation testing report" in mutation_block
    assert "Fails when" not in mutation_block
    assert "continue-on-error: true" in mutation_block
    assert "|| true" in mutation_block


def test_diff_coverage_report_comment_matches_advisory_behavior() -> None:
    """Diff coverage should be called a report while artifact download can fail open."""
    text = WORKFLOW.read_text(encoding="utf-8")
    diff_block = text.split("  diff-coverage:", 1)[1].split("  pyright-strict-zone:", 1)[0]

    assert "Diff coverage report" in diff_block
    assert "diff-cover report" in diff_block
    job_name = _job("diff-coverage").get("name", "")
    assert isinstance(job_name, str)
    assert "gate" not in job_name.lower()
    assert "continue-on-error: true" in diff_block
