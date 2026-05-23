"""Tests for the Sonar tracker workflow wiring."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class RenderSyncEnv(TypedDict):
    """Environment block used by the tracker sync step."""

    GH_TOKEN: str
    GITHUB_TOKEN: str


class WorkflowStep(TypedDict, total=False):
    """Subset of a GitHub Actions step used by this test."""

    name: str
    env: RenderSyncEnv


class RenderJob(TypedDict):
    """Subset of the tracker render job used by this test."""

    steps: list[WorkflowStep]


class WorkflowJobs(TypedDict):
    """Workflow jobs needed by this test."""

    render: RenderJob


def test_sonar_tracker_workflow_exports_gh_token_for_cli() -> None:
    """The tracker workflow must authenticate GitHub CLI operations."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "sonar-tracker.yml").read_text())
    jobs = cast("WorkflowJobs", workflow["jobs"])
    render_job = jobs["render"]
    steps = render_job["steps"]
    sync_step = next(step for step in steps if step.get("name") == "Render and sync tracker")
    env = sync_step["env"]

    assert env["GH_TOKEN"] == "${{ github.token }}"
    assert env["GITHUB_TOKEN"] == "${{ github.token }}"
