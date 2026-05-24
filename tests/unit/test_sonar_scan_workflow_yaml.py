"""Structural assertions on the SonarQube scan workflow.

These tests pin the contract that the Sonar scan consumes the coverage
artifact from a successful CI run for the same main commit. The scan
must not start on the raw push event or on a cancelled CI run before the
matching CI coverage artifact exists.

The tests below assert the workflow has:

    * a ``workflow_run`` trigger for completed main CI runs,
    * a job guard that accepts only successful main CI workflow runs,
    * no direct ``push`` trigger, because that races the CI artifact,
    * a workflow-run artifact download keyed to the triggering CI run,
    * a thin fallback limited to manual dispatch only.

The tests are cheap; they parse YAML only and do not call the GitHub
API.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


SONAR_WF = Path(".github/workflows/sonar-scan.yml")


@pytest.fixture(scope="module")
def sonar_doc() -> dict[str, object]:
    """Parse the sonar-scan workflow once per module."""
    return yaml.safe_load(SONAR_WF.read_text(encoding="utf-8"))


def test_sonar_scan_workflow_file_exists() -> None:
    """The Sonar scan workflow must exist at the expected path."""
    assert SONAR_WF.is_file(), f"Missing workflow: {SONAR_WF}"


def test_sonar_scan_triggers_after_main_ci_completion(sonar_doc: dict[str, object]) -> None:
    """Sonar scan must trigger after the main CI run reaches a terminal state.

    This avoids the push-time race where Sonar starts before the
    matching ``coverage-report`` artifact has been uploaded.
    """
    on_block = sonar_doc.get(True) or sonar_doc.get("on")
    assert isinstance(on_block, dict), f"Workflow `on:` block must be a mapping, got {type(on_block)!r}"
    workflow_run = on_block.get("workflow_run")
    assert isinstance(workflow_run, dict), "Sonar scan workflow must define a `workflow_run:` trigger"
    assert workflow_run.get("workflows") == ["CI"]
    assert workflow_run.get("branches") == ["main"]
    assert workflow_run.get("types") == ["completed"]
    assert "push" not in on_block, "Sonar scan must not race CI coverage on raw push events"


def test_sonar_scan_keeps_workflow_dispatch(sonar_doc: dict[str, object]) -> None:
    """``workflow_dispatch`` must remain so operators can bootstrap."""
    on_block = sonar_doc.get(True) or sonar_doc.get("on")
    assert isinstance(on_block, dict)
    assert "workflow_dispatch" in on_block, "Sonar scan must keep its manual dispatch trigger"


def test_sonar_scan_job_if_accepts_successful_workflow_run_and_dispatch(sonar_doc: dict[str, object]) -> None:
    """The job-level `if` must accept successful CI runs and manual dispatch."""
    jobs = sonar_doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, "Workflow must declare a `jobs:` block"
    scan_job = jobs.get("scan")
    assert isinstance(scan_job, dict), "Workflow must define a `scan` job"
    job_if = scan_job.get("if", "")
    assert isinstance(job_if, str)
    flat = " ".join(job_if.split())
    assert "workflow_dispatch" in flat, "Job `if` must accept workflow_dispatch events"
    assert "workflow_run" in flat, "Job `if` must accept workflow_run events from CI"
    assert "github.event.workflow_run.conclusion == 'success'" in flat, (
        "Workflow-run scans must ignore cancelled or failed CI runs that do not produce coverage artifacts"
    )
    assert "head_branch" in flat and "main" in flat, "Workflow-run scans must stay pinned to main"


def test_sonar_scan_downloads_workflow_run_coverage_artifact(sonar_doc: dict[str, object]) -> None:
    """Workflow-run scans must download coverage from the triggering CI run."""
    jobs = sonar_doc.get("jobs", {})
    assert isinstance(jobs, dict)
    scan = jobs.get("scan", {})
    assert isinstance(scan, dict)
    steps = scan.get("steps", [])
    assert isinstance(steps, list) and steps, "scan job must declare steps"

    download_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("name") == "Download coverage artifact (workflow_run)"
        and "actions/download-artifact" in str(step.get("uses", ""))
    ]
    assert len(download_steps) == 1
    with_block = download_steps[0].get("with") or {}
    assert isinstance(with_block, dict)
    assert with_block.get("name") == "coverage-report"
    assert with_block.get("run-id") == "${{ github.event.workflow_run.id }}"


def test_sonar_scan_limits_thin_fallback_to_manual_dispatch(sonar_doc: dict[str, object]) -> None:
    """Thin fallback must not replace missing main CI coverage.

    A push or workflow-run scan without the matching CI artifact should
    skip the scan instead of reporting a partial coverage number.
    """
    jobs = sonar_doc.get("jobs", {})
    assert isinstance(jobs, dict)
    scan = jobs.get("scan", {})
    assert isinstance(scan, dict)
    steps = scan.get("steps", [])
    assert isinstance(steps, list) and steps, "scan job must declare steps"

    step_names = [s.get("name", "") for s in steps if isinstance(s, dict)]
    fallback_steps = [step for step in steps if isinstance(step, dict) and step.get("name") == "Thin coverage fallback"]
    assert len(fallback_steps) == 1, f"Found steps: {step_names!r}"
    fallback_if = str(fallback_steps[0].get("if", ""))
    assert "github.event_name == 'workflow_dispatch'" in fallback_if
    assert "workflow_run" not in fallback_if

    sonar_steps = [
        step
        for step in steps
        if isinstance(step, dict) and "SonarSource/sonarqube-scan-action" in str(step.get("uses", ""))
    ]
    assert len(sonar_steps) == 1
    sonar_if = str(sonar_steps[0].get("if", ""))
    assert "steps.scan_coverage.outputs.available == 'true'" in sonar_if, (
        "Sonar scan must require a usable coverage.xml after artifact download or manual fallback"
    )


def test_sonar_scan_references_coverage_xml_in_args(sonar_doc: dict[str, object]) -> None:
    """The SonarQube scan step must point at coverage.xml so Sonar reads it."""
    jobs = sonar_doc.get("jobs", {})
    scan = jobs.get("scan", {})
    steps = scan.get("steps", [])
    sonar_step = next(
        (s for s in steps if isinstance(s, dict) and "SonarSource/sonarqube-scan-action" in (s.get("uses") or "")),
        None,
    )
    assert sonar_step is not None, "Workflow must invoke SonarSource/sonarqube-scan-action"
    args = (sonar_step.get("with") or {}).get("args", "")
    assert "sonar.python.coverage.reportPaths=coverage.xml" in args, (
        "Sonar scan args must point at coverage.xml so Python coverage is ingested"
    )


def test_sonar_scan_scope_comes_from_project_properties(sonar_doc: dict[str, object]) -> None:
    """The workflow must not clobber the canonical Sonar scope config."""
    jobs = sonar_doc.get("jobs", {})
    scan = jobs.get("scan", {})
    steps = scan.get("steps", [])
    sonar_step = next(
        (s for s in steps if isinstance(s, dict) and "SonarSource/sonarqube-scan-action" in (s.get("uses") or "")),
        None,
    )
    assert sonar_step is not None, "Workflow must invoke SonarSource/sonarqube-scan-action"
    args = (sonar_step.get("with") or {}).get("args", "")

    assert "sonar.sources=" not in args
    assert "sonar.tests=" not in args
    assert "sonar.exclusions=" not in args
    assert "sonar.coverage.exclusions=" not in args


def test_sonar_scan_revision_matches_workflow_run_head_sha(sonar_doc: dict[str, object]) -> None:
    """Workflow-run scans must report the same commit that was checked out."""
    jobs = sonar_doc.get("jobs", {})
    scan = jobs.get("scan", {})
    steps = scan.get("steps", [])
    sonar_step = next(
        (s for s in steps if isinstance(s, dict) and "SonarSource/sonarqube-scan-action" in (s.get("uses") or "")),
        None,
    )
    assert sonar_step is not None, "Workflow must invoke SonarSource/sonarqube-scan-action"
    args = (sonar_step.get("with") or {}).get("args", "")

    assert "github.event.workflow_run.head_sha" in args
