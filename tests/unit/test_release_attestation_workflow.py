"""Smoke test: the release-attestation workflow steps are present and well-formed.

Closes the FINOS AIGF CTRL-MODEL-SUPPLY-CHAIN release-artefact gap by
asserting that the publish workflow actually calls
``actions/attest-build-provenance`` against ``dist/*`` with the right
permissions. Without this guard the gap could silently re-open during
a workflow refactor.

The test parses the YAML and walks the job tree; it does not execute
the workflows. The end-to-end ``gh attestation verify`` against the
public attestations endpoint is the responsibility of the dedicated
release-attestation smoke job (see ``release-attestation`` workflow).

Scope note: the auto-release workflow tags and creates the GitHub
Release; the actual artefact build + Sigstore attestation + PyPI
publish lives in publish.yml (single-publisher consolidation, #1286).
The attest assertion therefore only applies to publish.yml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_WF = REPO_ROOT / ".github" / "workflows" / "publish.yml"

ATTEST_ACTION_PREFIX = "actions/attest-build-provenance"


def _load_yaml(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(path.read_text()))


def _all_steps(jobs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        for step in steps:
            if isinstance(step, dict):
                out.append((job_name, step))
    return out


def _job(data: dict[str, Any], job_name: str) -> dict[str, Any]:
    job = data["jobs"][job_name]
    assert isinstance(job, dict)
    return cast("dict[str, Any]", job)


def _step_run(data: dict[str, Any], job_name: str, step_name: str) -> str:
    job = _job(data, job_name)
    steps = job.get("steps") or []
    for step in steps:
        if isinstance(step, dict) and step.get("name") == step_name:
            run = step.get("run")
            assert isinstance(run, str)
            return run
    pytest.fail(f"{PUBLISH_WF.name}::{job_name} has no step named {step_name!r}")


@pytest.mark.parametrize("workflow_path", [PUBLISH_WF])
def test_workflow_yaml_parses(workflow_path: Path) -> None:
    """Workflow YAML is syntactically valid -- prevents typos breaking CI silently."""
    data = _load_yaml(workflow_path)
    assert isinstance(data, dict)
    assert "jobs" in data


@pytest.mark.parametrize("workflow_path", [PUBLISH_WF])
def test_workflow_calls_attest_build_provenance(workflow_path: Path) -> None:
    """At least one job step uses ``actions/attest-build-provenance@<ref>``."""
    data = _load_yaml(workflow_path)
    steps = _all_steps(data["jobs"])
    matching = [
        (job, step)
        for job, step in steps
        if isinstance(step.get("uses"), str) and step["uses"].startswith(ATTEST_ACTION_PREFIX)
    ]
    assert matching, (
        f"{workflow_path.name} no longer calls actions/attest-build-provenance -- "
        "FINOS AIGF CTRL-MODEL-SUPPLY-CHAIN release-artefact gap would re-open"
    )


@pytest.mark.parametrize("workflow_path", [PUBLISH_WF])
def test_attest_step_has_subject_path(workflow_path: Path) -> None:
    """The attest step must declare subject-path so ``dist/*`` is actually attested."""
    data = _load_yaml(workflow_path)
    steps = _all_steps(data["jobs"])
    for _job, step in steps:
        if isinstance(step.get("uses"), str) and step["uses"].startswith(ATTEST_ACTION_PREFIX):
            with_block = step.get("with") or {}
            assert "subject-path" in with_block, (
                f"{workflow_path.name}: attest step missing subject-path -- nothing would be signed"
            )
            assert "dist" in str(with_block["subject-path"])


@pytest.mark.parametrize("workflow_path", [PUBLISH_WF])
def test_attest_job_has_required_permissions(workflow_path: Path) -> None:
    """The job hosting the attest step must declare id-token: write + attestations: write."""
    data = _load_yaml(workflow_path)
    jobs = data["jobs"]
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps") or []
        has_attest = any(
            isinstance(s, dict) and isinstance(s.get("uses"), str) and s["uses"].startswith(ATTEST_ACTION_PREFIX)
            for s in steps
        )
        if not has_attest:
            continue
        perms = job.get("permissions") or {}
        # Permissions block can be an empty dict, a single string, or a mapping.
        # We only need the mapping form here -- attest needs writable scopes.
        assert isinstance(perms, dict), (
            f"{workflow_path.name}::{job_name} declares permissions as a string; "
            "attest needs explicit id-token: write + attestations: write"
        )
        assert perms.get("id-token") == "write", (
            f"{workflow_path.name}::{job_name} missing id-token: write -- Sigstore keyless OIDC will fail at runtime"
        )
        assert perms.get("attestations") == "write", (
            f"{workflow_path.name}::{job_name} missing attestations: write -- "
            "the GitHub attestations API will reject the upload"
        )


def test_attest_action_pinned_to_commit_sha() -> None:
    """The attest action ref is pinned to a 40-char commit sha (Sonar S7409 / supply-chain)."""
    data = _load_yaml(PUBLISH_WF)
    steps = _all_steps(data["jobs"])
    for _job, step in steps:
        uses = step.get("uses")
        if isinstance(uses, str) and uses.startswith(ATTEST_ACTION_PREFIX):
            ref = uses.split("@", 1)[1] if "@" in uses else ""
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                f"actions/attest-build-provenance must be pinned to a 40-char sha, got: {uses}"
            )
            return
    pytest.fail("publish.yml has no attest-build-provenance step")


def test_protocol_gate_does_not_ignore_install_or_pytest_failures() -> None:
    """The protocol gate must fail when dependency install or pytest exits non-zero."""
    data = _load_yaml(PUBLISH_WF)
    run = _step_run(data, "protocol-gate", "Run protocol compatibility check")
    unsafe_lines = [
        line.strip() for line in run.splitlines() if line.strip().startswith(("uv pip install", "uv run pytest"))
    ]
    assert unsafe_lines
    assert all("|| true" not in line for line in unsafe_lines), unsafe_lines


def test_protocol_gate_status_uses_pytest_exit_code() -> None:
    """The compat JSON status must use pytest's exit code rather than output substrings."""
    data = _load_yaml(PUBLISH_WF)
    run = _step_run(data, "protocol-gate", "Run protocol compatibility check")
    assert '"FAILED" not in test_output' not in run
    assert "pytest_exit_code" in run


def test_publish_test_job_runs_release_tests() -> None:
    """The release guard test job must run tests, not only lint."""
    data = _load_yaml(PUBLISH_WF)
    job = _job(data, "test")
    assert job.get("name") == "Verify tests pass"

    runs = [step["run"] for step in job.get("steps", []) if isinstance(step, dict) and isinstance(step.get("run"), str)]
    assert "uv run python scripts/run_tests.py -k release -x" in "\n".join(runs)


def test_github_release_uploads_and_asserts_dist_assets() -> None:
    """Existing GitHub Releases must receive dist assets and fail if assets are absent."""
    data = _load_yaml(PUBLISH_WF)
    run = _step_run(data, "github-release", "Create release")
    assert "|| echo" not in run
    assert "gh release upload" in run
    assert "--clobber" in run
    assert "gh release view" in run
    assert "asset_count" in run
