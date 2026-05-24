"""Structural assertions on ``.github/workflows/zizmor.yml``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "zizmor.yml"


StepSpec = TypedDict(
    "StepSpec",
    {"name": object, "uses": object, "with": object, "env": object},
    total=False,
)


class JobSpec(TypedDict, total=False):
    """Subset of a GitHub Actions job used by these tests."""

    steps: list[object]


class WorkflowSpec(TypedDict, total=False):
    """Subset of a GitHub Actions workflow used by these tests."""

    jobs: Mapping[str, object]


def _load_workflow() -> WorkflowSpec:
    loaded: object = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{WORKFLOW} must parse as a mapping, got {type(loaded).__name__}")
    return cast("WorkflowSpec", loaded)


def _zizmor_step(workflow: WorkflowSpec) -> StepSpec:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, Mapping)
    job = jobs.get("zizmor")
    assert isinstance(job, dict)
    job_spec = cast("JobSpec", job)
    steps = job_spec.get("steps")
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == "Run zizmor":
            return cast("StepSpec", step)
    pytest.fail("zizmor workflow must include a `Run zizmor` step")


def test_zizmor_required_check_runs_offline_audits() -> None:
    """The required workflow must not false-red on default-token online tag lookups."""
    workflow = _load_workflow()
    step = _zizmor_step(workflow)
    with_block = step.get("with")

    assert isinstance(with_block, dict)
    assert with_block.get("advanced-security") is True
    assert with_block.get("online-audits") is False
    env_block = step.get("env", {})
    assert isinstance(env_block, Mapping)
    assert "GH_TOKEN" not in env_block
