"""Structural assertions on ``.github/workflows/bernstein-ci-fix.yml``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dev env should have pyyaml
    pytest.skip("pyyaml not installed", allow_module_level=True)


WORKFLOW = Path(".github/workflows/bernstein-ci-fix.yml")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow(workflow_text: str) -> dict[str, object]:
    return yaml.safe_load(workflow_text)


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get(name)
    assert isinstance(job, dict), f"expected job {name!r}"
    return job


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _step(job: dict[str, object], *, name: str) -> dict[str, object]:
    match = next((step for step in _steps(job) if step.get("name") == name), None)
    assert match is not None, f"expected step named {name!r}"
    return match


def test_fallback_issue_requires_successful_triage_and_metadata(workflow: dict[str, object]) -> None:
    fallback = _job(workflow, "fallback-issue")
    condition = fallback.get("if", "")
    assert isinstance(condition, str)
    flat = " ".join(condition.split())

    assert "needs.triage.result == 'success'" in flat, (
        "fallback-issue must not run when triage is skipped by feature flags or recursion guards"
    )
    assert "needs.triage.outputs.head_sha != ''" in flat, "fallback issue must require non-empty head_sha"
    assert "needs.triage.outputs.run_id != ''" in flat, "fallback issue must require non-empty run_id"


def test_tier3_setup_uv_uses_pinned_version(workflow: dict[str, object]) -> None:
    tier3 = _job(workflow, "tier3-shadow")
    setup_uv = _step(tier3, name="Set up Python via uv")
    with_block = setup_uv.get("with")
    assert isinstance(with_block, dict)
    version = with_block.get("version")
    assert isinstance(version, str), "Tier-3 setup-uv must pin an explicit uv version"
    assert version != "latest", "Tier-3 setup-uv must not use the mutable 'latest' alias"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"expected semver uv pin, got {version!r}"


def test_tier3_checkout_uses_trusted_ref_then_reachable_sha_guard(
    workflow: dict[str, object],
) -> None:
    tier3 = _job(workflow, "tier3-shadow")
    steps = _steps(tier3)

    checkout = next((step for step in steps if "actions/checkout" in str(step.get("uses", ""))), None)
    assert checkout is not None, "Tier-3 job must check out repository contents"
    with_block = checkout.get("with")
    assert isinstance(with_block, dict)
    assert with_block.get("ref") == "main", "Tier-3 checkout must start from trusted main, not caller head_sha"
    assert with_block.get("fetch-depth") == 0, "Tier-3 checkout must fetch history for the ancestry guard"

    guard = _step(tier3, name="Verify and pin to failing commit")
    env = guard.get("env")
    assert isinstance(env, dict)
    assert env.get("HEAD_SHA") == "${{ needs.triage.outputs.head_sha }}"
    run = guard.get("run", "")
    assert isinstance(run, str)
    assert "grep -qE '^[0-9a-f]{40}$'" in run, "Tier-3 guard must reject non-40-hex SHAs"
    assert 'git merge-base --is-ancestor "$HEAD_SHA" origin/main' in run, (
        "Tier-3 guard must require the SHA to be reachable from origin/main"
    )
    assert 'git -c advice.detachedHead=false checkout "$HEAD_SHA"' in run


def test_workflow_uses_only_sha_pinned_actions(workflow_text: str) -> None:
    uses_lines = [line.strip() for line in workflow_text.splitlines() if line.strip().startswith("uses: ")]
    for line in uses_lines:
        if line == "uses: ./":
            continue
        uses_ref = line.split("#", 1)[0].strip().removeprefix("uses: ")
        assert "@" in uses_ref, f"action is not pinned: {line}"
        _, ref = uses_ref.rsplit("@", 1)
        assert SHA_PATTERN.fullmatch(ref), f"action must be pinned to a 40-char SHA: {line}"
