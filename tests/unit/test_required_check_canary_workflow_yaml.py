"""Structural assertions on the required-check name canary.

The canary in ``.github/workflows/required-check-canary.yml`` defends the
single `CI gate` required context configured in branch protection on
`main`. These tests guard the canary itself plus the in-tree invariants
the canary asserts at workflow-run time, so that a refactor cannot
weaken the canary AND drift the required context in the same PR.

Invariants exercised here:

1. ``ci.yml`` exposes a ``ci-gate`` job whose ``name:`` is exactly
   ``CI gate``.
2. ``ci.yml`` exposes a ``test-macos`` job whose ``name:`` is the
   literal string ``Test (macos-latest, Python 3.13)`` (no ``${{ ... }}``
   template, which would resolve to a different string when the job is
   skipped via the gate condition).
3. No other job across ``.github/workflows/*.yml`` emits a check-run
   named ``CI gate``. The required context must be uniquely produced.
4. The canary workflow file itself exists and is wired to the
   ``pull_request``/``schedule``/``workflow_dispatch`` triggers, with
   every action SHA-pinned and the verify step asserting the same set
   of invariants.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


CI = Path(".github/workflows/ci.yml")
CANARY = Path(".github/workflows/required-check-canary.yml")
WORKFLOWS_DIR = Path(".github/workflows")

REQUIRED_CONTEXT = "CI gate"
REQUIRED_JOB_KEY = "ci-gate"
MACOS_JOB_KEY = "test-macos"
MACOS_JOB_NAME = "Test (macos-latest, Python 3.13)"


@pytest.fixture(scope="module")
def ci_doc() -> dict[str, object]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def canary_text() -> str:
    return CANARY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def canary_doc(canary_text: str) -> dict[str, object]:
    return yaml.safe_load(canary_text)


# ---------------------------------------------------------------------------
# Invariants on ci.yml that branch protection depends on
# ---------------------------------------------------------------------------


def test_ci_gate_job_exists(ci_doc: dict[str, object]) -> None:
    jobs = ci_doc.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get(REQUIRED_JOB_KEY)
    assert isinstance(job, dict), (
        f"ci.yml must keep a `{REQUIRED_JOB_KEY}` job -- it produces the `{REQUIRED_CONTEXT}` required check on `main`."
    )


def test_ci_gate_name_is_literal_required_context(ci_doc: dict[str, object]) -> None:
    jobs = ci_doc.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs[REQUIRED_JOB_KEY]
    assert isinstance(job, dict)
    assert job.get("name") == REQUIRED_CONTEXT, (
        f"ci-gate.name must equal {REQUIRED_CONTEXT!r}. "
        "Branch protection's required context is keyed on this exact string."
    )


def test_test_macos_name_is_literal(ci_doc: dict[str, object]) -> None:
    jobs = ci_doc.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get(MACOS_JOB_KEY)
    assert isinstance(job, dict)
    name = job.get("name")
    assert isinstance(name, str)
    assert "${{" not in name and "}}" not in name, (
        f"`{MACOS_JOB_KEY}.name` must NOT be templated. "
        "Skip-state check runs post the unresolved template, breaking any "
        "downstream required-context rule keyed on the literal form."
    )
    assert name == MACOS_JOB_NAME, (
        f"`{MACOS_JOB_KEY}.name` is {name!r}; expected {MACOS_JOB_NAME!r}. "
        "If the rename is intentional, update the canary expectation in the "
        "same PR."
    )


def test_ci_gate_check_run_name_is_unique_across_workflows() -> None:
    collisions: list[str] = []
    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        if not isinstance(wf, dict):
            continue
        jobs = wf.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for key, body in jobs.items():
            if not isinstance(body, dict):
                continue
            if body.get("name") != REQUIRED_CONTEXT:
                continue
            if wf_path == CI and key == REQUIRED_JOB_KEY:
                continue
            collisions.append(f"{wf_path}:{key}")
    assert not collisions, (
        f"Multiple jobs emit a check named {REQUIRED_CONTEXT!r}: {collisions}. "
        "Branch protection's single required context must be uniquely produced."
    )


# ---------------------------------------------------------------------------
# Invariants on the canary workflow itself
# ---------------------------------------------------------------------------


def test_canary_workflow_exists() -> None:
    assert CANARY.exists(), "required-check name canary workflow is missing"


def test_canary_has_pull_request_schedule_and_dispatch_triggers(
    canary_doc: dict[str, object],
) -> None:
    # PyYAML 1.1 parses bare ``on:`` as the boolean True; tolerate both.
    on = canary_doc.get(True, canary_doc.get("on"))
    assert isinstance(on, dict)
    assert "pull_request" in on, "canary must run on PRs that touch workflow files"
    assert "schedule" in on, "canary must run on a weekly cron"
    assert "workflow_dispatch" in on, "canary must be manually runnable"


def test_canary_pull_request_paths_filtered_to_workflows(
    canary_doc: dict[str, object],
) -> None:
    on = canary_doc.get(True, canary_doc.get("on"))
    assert isinstance(on, dict)
    pr = on.get("pull_request")
    assert isinstance(pr, dict)
    paths = pr.get("paths") or []
    assert any(".github/workflows/" in p for p in paths), "canary should only fire on PRs that modify workflow files"


def test_canary_actions_pinned_to_sha(canary_text: str) -> None:
    """Every ``uses:`` must reference a 40-char SHA, not a tag."""
    uses_lines = [m.group(0) for m in re.finditer(r"uses:\s*[^\s#]+", canary_text)]
    pattern = re.compile(r"uses:\s*[\w./-]+@[0-9a-f]{40}\b")
    for line in uses_lines:
        assert pattern.match(line), f"action not pinned to 40-char SHA: {line}"


def test_canary_permissions_locked_down(canary_doc: dict[str, object]) -> None:
    # Workflow-level permissions are empty; job-level grants only `contents: read`.
    perms = canary_doc.get("permissions")
    assert perms == {} or perms == "{}"
    jobs = canary_doc.get("jobs")
    assert isinstance(jobs, dict)
    verify = jobs.get("verify")
    assert isinstance(verify, dict)
    job_perms = verify.get("permissions")
    assert isinstance(job_perms, dict)
    assert job_perms == {"contents": "read"}


def test_canary_asserts_required_context_name(canary_text: str) -> None:
    """The literal expected context names must appear in the canary env block."""
    assert f'REQUIRED_CONTEXT: "{REQUIRED_CONTEXT}"' in canary_text
    assert f'REQUIRED_JOB_KEY: "{REQUIRED_JOB_KEY}"' in canary_text
    assert f'MACOS_JOB_KEY: "{MACOS_JOB_KEY}"' in canary_text
    assert f'MACOS_JOB_NAME: "{MACOS_JOB_NAME}"' in canary_text
