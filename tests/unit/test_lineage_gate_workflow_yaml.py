"""Structural assertions for the CI lineage gate workflow job."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

import yaml


class WorkflowStep(TypedDict, total=False):
    name: object
    run: object


class WorkflowJob(TypedDict, total=False):
    name: object
    steps: list[object]


class WorkflowFile(TypedDict, total=False):
    jobs: dict[str, WorkflowJob]


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load() -> WorkflowFile:
    return cast("WorkflowFile", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _job(name: str) -> WorkflowJob:
    jobs = _load().get("jobs", {})
    assert isinstance(jobs, dict)
    job = jobs.get(name)
    assert isinstance(job, dict), f"expected job {name!r}"
    return job


def _step(name: str) -> WorkflowStep:
    steps = _job("lineage-gate").get("steps", [])
    assert isinstance(steps, list)
    for step_value in steps:
        if not isinstance(step_value, dict):
            continue
        step = cast("WorkflowStep", step_value)
        if step.get("name") == name:
            return step
    raise AssertionError(f"expected lineage-gate step {name!r}")


def _run(name: str) -> str:
    run = _step(name).get("run", "")
    assert isinstance(run, str)
    return run


def test_lineage_gate_generates_checked_fixture_instead_of_skipping() -> None:
    """CI should verify a concrete lineage fixture when runtime state is absent."""
    run = _run("Run lineage gate")

    assert "No .sdd/lineage/log.jsonl" not in run
    assert "no-op PASS" not in run
    assert "if [ -f .sdd/lineage/log.jsonl ]" not in run
    assert "generate_keypair" in run
    assert "sign_detached" in run
    assert "scripts/check_lineage.py" in run
    assert 'LINEAGE_FIXTURE="${RUNNER_TEMP}/lineage-fixture"' in run
    assert '--log "${LINEAGE_FIXTURE}/lineage/log.jsonl"' in run
    assert '--cards "${LINEAGE_FIXTURE}/agents"' in run
