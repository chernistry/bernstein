"""Structural assertions on the SonarQube scan workflow.

These tests pin the contract that the Sonar scan stays reachable on
main without depending on a successful upstream CI conclusion. The
root cause of the historical "all sonar-scan runs return skipped" was
the job-level ``if: workflow_run.conclusion == 'success'`` combined
with ``ci.yml``'s ``cancel-in-progress: true`` policy on main: a
rapid merge cadence cancels almost every CI run before it finishes,
so the success conclusion is never observed.

The tests below assert the workflow has:

    * a direct ``push: branches: [main]`` trigger so the scan runs
      independently of CI's conclusion,
    * a fallback coverage step that activates when no upstream
      ``coverage-report`` artifact is available,
    * a job-level ``if`` that accepts ``push`` and ``workflow_dispatch``
      events, not only the historical workflow_run success.

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


def test_sonar_scan_triggers_on_push_to_main(sonar_doc: dict[str, object]) -> None:
    """Sonar scan must trigger directly on push to main.

    Without this trigger the scan relied entirely on
    ``workflow_run.conclusion == 'success'``, which under the normal
    rapid-merge cadence almost never fires because ci.yml cancels
    in-flight runs on each new push. The push trigger is the only
    one that guarantees the scan reaches main on every merge.
    """
    on_block = sonar_doc.get(True) or sonar_doc.get("on")
    assert isinstance(on_block, dict), f"Workflow `on:` block must be a mapping, got {type(on_block)!r}"
    push_block = on_block.get("push")
    assert isinstance(push_block, dict), "Sonar scan workflow must define a `push:` trigger"
    branches = push_block.get("branches") or []
    assert "main" in branches, "Sonar scan `push` trigger must include `main`"


def test_sonar_scan_keeps_workflow_dispatch(sonar_doc: dict[str, object]) -> None:
    """``workflow_dispatch`` must remain so operators can bootstrap."""
    on_block = sonar_doc.get(True) or sonar_doc.get("on")
    assert isinstance(on_block, dict)
    assert "workflow_dispatch" in on_block, "Sonar scan must keep its manual dispatch trigger"


def test_sonar_scan_job_if_accepts_push_and_dispatch(sonar_doc: dict[str, object]) -> None:
    """The job-level `if` must accept `push` and `workflow_dispatch`.

    Regression guard: an earlier version gated the job purely on
    ``github.event.workflow_run.conclusion == 'success'``, which never
    fires under the rapid-merge cancellation pattern.
    """
    jobs = sonar_doc.get("jobs")
    assert isinstance(jobs, dict) and jobs, "Workflow must declare a `jobs:` block"
    scan_job = jobs.get("scan")
    assert isinstance(scan_job, dict), "Workflow must define a `scan` job"
    job_if = scan_job.get("if", "")
    assert isinstance(job_if, str)
    flat = " ".join(job_if.split())
    assert "workflow_dispatch" in flat, "Job `if` must accept workflow_dispatch events"
    assert "'push'" in flat or '"push"' in flat or "push" in flat, (
        "Job `if` must accept push events so a direct push to main is not gated by upstream CI conclusion"
    )


def test_sonar_scan_has_coverage_fallback(sonar_doc: dict[str, object]) -> None:
    """Workflow must have a thin coverage fallback for cancelled CI runs.

    When ci.yml cancels in-flight runs the ``coverage-report``
    artifact may be missing or empty. The Sonar workflow must run a
    thin coverage pass in that case so the project never sits at
    Coverage=0 forever.
    """
    jobs = sonar_doc.get("jobs", {})
    assert isinstance(jobs, dict)
    scan = jobs.get("scan", {})
    assert isinstance(scan, dict)
    steps = scan.get("steps", [])
    assert isinstance(steps, list) and steps, "scan job must declare steps"

    step_names = [s.get("name", "") for s in steps if isinstance(s, dict)]
    has_fallback = any("fallback" in name.lower() or "thin coverage" in name.lower() for name in step_names)
    assert has_fallback, (
        "Sonar scan must include a thin coverage fallback step so "
        "Coverage stays > 0 even when the upstream CI artifact is missing. "
        f"Found steps: {step_names!r}"
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
