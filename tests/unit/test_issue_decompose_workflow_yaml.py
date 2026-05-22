"""Structural assertions for the issue decomposition workflow."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict, cast

import yaml

WorkflowStep = TypedDict(
    "WorkflowStep",
    {
        "name": object,
        "uses": object,
        "run": object,
        "env": object,
        "with": object,
    },
    total=False,
)

WorkflowJob = TypedDict(
    "WorkflowJob",
    {
        "if": object,
        "permissions": object,
        "steps": list[object],
    },
    total=False,
)


class WorkflowFile(TypedDict, total=False):
    jobs: dict[str, WorkflowJob]


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bernstein-issues-decompose.yml"


def _load() -> WorkflowFile:
    return cast("WorkflowFile", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _job(name: str) -> WorkflowJob:
    jobs = _load().get("jobs", {})
    assert isinstance(jobs, dict)
    job = jobs.get(name)
    assert isinstance(job, dict), f"expected job {name!r}"
    return job


def _steps(job: WorkflowJob) -> list[WorkflowStep]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [cast("WorkflowStep", step) for step in steps if isinstance(step, dict)]


def test_decompose_job_requires_trusted_issue_author() -> None:
    job = _job("decompose")
    condition = job.get("if", "")

    assert isinstance(condition, str)
    assert "github.event.label.name == 'bernstein'" in condition
    assert "github.event.issue.author_association" in condition
    assert "OWNER" in condition
    assert "MEMBER" in condition
    assert "COLLABORATOR" in condition


def test_untrusted_issue_path_does_not_run_agent_or_receive_llm_secret() -> None:
    job = _job("reject-untrusted-issue")
    condition = job.get("if", "")
    permissions = job.get("permissions", {})

    assert isinstance(condition, str)
    assert "github.event.issue.author_association" in condition
    assert isinstance(permissions, dict)
    assert permissions == {"issues": "write"}

    for step in _steps(job):
        assert step.get("uses") != "./"
        env = step.get("env", {})
        assert not isinstance(env, dict) or "ANTHROPIC_API_KEY" not in env
