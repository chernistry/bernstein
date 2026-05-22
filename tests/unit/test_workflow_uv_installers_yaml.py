"""Structural checks for uv installer usage in selected workflows."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

OWNED_WORKFLOWS = (
    "sonar-scan.yml",
    "docs-observability-snapshot.yml",
    "pr-observability-summary.yml",
    "glitchtip-ingester.yml",
    "nightly-canary.yml",
    "sweep-sonar-findings.yml",
)

CURL_PIPE_SH_RE = re.compile(r"curl\b[^\n|]*\|\s*(?:ba)?sh\b")
UNPINNED_PIP_UV_RE = re.compile(r"\bpip\s+install\b[^\n]*\buv\b")


def _load_workflow(path: Path) -> dict[str, object]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{path} must parse as a YAML mapping"
    return parsed


def _iter_steps(value: object) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    if isinstance(value, dict):
        if isinstance(value.get("steps"), list):
            steps.extend(step for step in value["steps"] if isinstance(step, dict))
        for child in value.values():
            steps.extend(_iter_steps(child))
    elif isinstance(value, list):
        for child in value:
            steps.extend(_iter_steps(child))
    return steps


@pytest.mark.parametrize("workflow_name", OWNED_WORKFLOWS)
def test_owned_workflows_do_not_install_uv_with_curl_pipe_shell(workflow_name: str) -> None:
    """Owned workflows must not pipe a live installer script into a shell."""
    workflow = WORKFLOWS_DIR / workflow_name
    text = workflow.read_text(encoding="utf-8")
    assert not CURL_PIPE_SH_RE.search(text), f"{workflow_name} must not install uv with `curl ... | sh`"


@pytest.mark.parametrize("workflow_name", OWNED_WORKFLOWS)
def test_owned_workflows_do_not_install_uv_with_unpinned_pip(workflow_name: str) -> None:
    """Owned workflows must not install uv from an unpinned pip requirement."""
    workflow = WORKFLOWS_DIR / workflow_name
    text = workflow.read_text(encoding="utf-8")
    assert not UNPINNED_PIP_UV_RE.search(text), f"{workflow_name} must not install uv with unpinned pip"


@pytest.mark.parametrize(
    "workflow_name",
    (
        "docs-observability-snapshot.yml",
        "pr-observability-summary.yml",
        "glitchtip-ingester.yml",
        "nightly-canary.yml",
        "sweep-sonar-findings.yml",
    ),
)
def test_project_install_workflows_use_local_bootstrap_action(workflow_name: str) -> None:
    """Project install workflows must use the repo bootstrap action for uv setup."""
    workflow = WORKFLOWS_DIR / workflow_name
    steps = _iter_steps(_load_workflow(workflow))
    uses_values = [step.get("uses") for step in steps]
    assert "./.github/actions/bootstrap" in uses_values
