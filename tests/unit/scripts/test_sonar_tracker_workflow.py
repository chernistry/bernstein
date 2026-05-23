"""Tests for the Sonar tracker workflow wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def test_sonar_tracker_workflow_exports_gh_token_for_cli() -> None:
    """The tracker workflow must authenticate GitHub CLI operations."""
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "sonar-tracker.yml").read_text())
    jobs = cast("dict[str, Any]", workflow["jobs"])
    render_job = cast("dict[str, Any]", jobs["render"])
    steps = cast("list[dict[str, Any]]", render_job["steps"])
    sync_step = next(step for step in steps if step.get("name") == "Render and sync tracker")
    env = cast("dict[str, str]", sync_step["env"])

    assert env["GH_TOKEN"] == "${{ github.token }}"
