"""Structural assertions for the release reconciliation workflow."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

import yaml

WorkflowStep = TypedDict(
    "WorkflowStep",
    {
        "name": object,
        "id": object,
        "if": object,
        "env": object,
        "run": object,
    },
    total=False,
)


class WorkflowJob(TypedDict, total=False):
    steps: list[object]
    permissions: dict[str, object]


class WorkflowFile(TypedDict, total=False):
    jobs: dict[str, WorkflowJob]


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "reconcile-release.yml"


def _load() -> WorkflowFile:
    return cast("WorkflowFile", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _steps() -> list[WorkflowStep]:
    jobs = _load().get("jobs", {})
    assert isinstance(jobs, dict)
    job = jobs.get("reconcile")
    assert isinstance(job, dict), "expected reconcile job"
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [cast("WorkflowStep", step) for step in steps if isinstance(step, dict)]


def _step(name: str) -> WorkflowStep:
    match = next((step for step in _steps() if step.get("name") == name), None)
    assert match is not None, f"expected step {name!r}"
    return match


def _run(step: WorkflowStep) -> str:
    run = step.get("run", "")
    assert isinstance(run, str)
    return run


def test_compare_step_audits_github_release_assets() -> None:
    """Release reconciliation must detect tag releases that exist without dist assets."""
    run = _run(_step("Compare versions and release assets"))

    assert '"gh",' in run
    assert '"release",' in run
    assert '"view",' in run
    assert '"--json",' in run
    assert '"assets",' in run
    assert "asset_count" in run
    assert "missing_assets = release_exists and asset_count == 0" in run
    assert "missing_assets=" in run
    assert "drift = version_drift or missing_assets" in run


def test_drift_issue_includes_missing_asset_context() -> None:
    """The tracking issue should open for missing assets and include asset evidence."""
    step = _step("Open or update drift issue (idempotent)")
    condition = step.get("if", "")
    assert isinstance(condition, str)
    assert "steps.cmp.outputs.drift == 'true'" in condition

    env = step.get("env", {})
    assert isinstance(env, dict)
    assert "MISSING_ASSETS" in env
    assert "ASSET_COUNT" in env

    run = _run(step)
    assert "GitHub Release missing dist assets" in run
    assert "GitHub Release asset count" in run


def test_no_drift_notice_reports_asset_count() -> None:
    """A clean reconciliation should make the checked asset count visible in logs."""
    step = _step("No drift")
    env = step.get("env", {})
    assert isinstance(env, dict)
    assert "ASSET_COUNT" in env
    assert "MISSING_ASSETS" in env

    run = _run(step)
    assert "assets=${ASSET_COUNT}" in run
