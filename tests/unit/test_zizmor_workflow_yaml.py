"""Structural assertions on ``.github/workflows/zizmor.yml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "zizmor.yml"


def _load_workflow() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _zizmor_step(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("zizmor")
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == "Run zizmor":
            return step
    pytest.fail("zizmor workflow must include a `Run zizmor` step")


def test_zizmor_required_check_runs_offline_audits() -> None:
    """The required workflow must not false-red on default-token online tag lookups."""
    workflow = _load_workflow()
    step = _zizmor_step(workflow)
    with_block = step.get("with")

    assert isinstance(with_block, dict)
    assert with_block.get("advanced-security") is True
    assert with_block.get("online-audits") is False
    assert "GH_TOKEN" not in (step.get("env") or {})
