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


GitHubScriptConfig = TypedDict(
    "GitHubScriptConfig",
    {"github-token": str, "script": str},
    total=False,
)

WorkflowPermissions = TypedDict(
    "WorkflowPermissions",
    {"contents": str, "issues": str, "pull-requests": str},
    total=False,
)

WorkflowStep = TypedDict(
    "WorkflowStep",
    {"name": str, "env": RenderSyncEnv, "uses": str, "with": GitHubScriptConfig},
    total=False,
)


class WorkflowJob(TypedDict):
    """Subset of a workflow job used by these tests."""

    steps: list[WorkflowStep]


class WorkflowJobs(TypedDict):
    """Workflow jobs needed by this test."""

    render: WorkflowJob
    comment: WorkflowJob


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


def test_sonar_pr_comment_workflow_can_update_issue_comments() -> None:
    """The Sonar PR comment workflow must grant issue-comment access."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "sonar-pr-comment.yml").read_text())
    permissions = cast("WorkflowPermissions", workflow["permissions"])
    jobs = cast("WorkflowJobs", workflow["jobs"])
    comment_job = jobs["comment"]
    script_step = next(step for step in comment_job["steps"] if step.get("name") == "Post or update sticky comment")
    script_config = script_step["with"]

    assert permissions["issues"] == "write"
    assert script_config["github-token"] == "${{ github.token }}"
