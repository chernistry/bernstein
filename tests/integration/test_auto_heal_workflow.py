"""Structural integration tests for ``.github/workflows/auto-heal.yml``.

These tests parse the workflow YAML and assert the safety-critical
properties documented in the workflow header:

* triggers on ``workflow_run`` of ``CI`` only (not itself),
* fires only on ``conclusion == 'failure'`` on ``main``,
* declares minimal permissions and never grants ``actions: write``,
* cordon allowlist regex actually matches the documented allowed
  paths,
* the workflow is well-formed YAML and references the two helper
  scripts and the categorizer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[2]
_WF = _REPO / ".github" / "workflows" / "auto-heal.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    text = _WF.read_text()
    # PyYAML interprets the bare-key ``on`` as boolean True, so re-key
    # if needed for downstream readability.
    data = yaml.safe_load(text)
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def test_workflow_file_exists() -> None:
    assert _WF.exists(), f"auto-heal workflow not found at {_WF}"


def test_workflow_parses_as_valid_yaml(workflow: dict) -> None:
    assert isinstance(workflow, dict)
    assert workflow.get("name") == "Auto-heal"


def test_workflow_run_trigger_targets_ci(workflow: dict) -> None:
    trigger = workflow["on"]
    assert "workflow_run" in trigger, trigger
    wr = trigger["workflow_run"]
    assert wr["workflows"] == ["CI"], wr["workflows"]
    assert wr["types"] == ["completed"], wr["types"]
    assert wr["branches"] == ["main"], wr["branches"]


def test_workflow_run_does_not_target_itself(workflow: dict) -> None:
    # Must NEVER list "Auto-heal" in the workflow_run workflows -- that
    # would be the recursion vector.
    wr = workflow["on"]["workflow_run"]
    assert "Auto-heal" not in wr["workflows"]


def test_workflow_top_level_permissions_empty(workflow: dict) -> None:
    # Job-level permissions only -- top-level is the empty dict.
    assert workflow.get("permissions") == {}, workflow.get("permissions")


def test_triage_job_filters_conclusion_failure(workflow: dict) -> None:
    triage = workflow["jobs"]["triage"]
    cond = triage["if"]
    assert "workflow_run.conclusion == 'failure'" in cond


def test_triage_job_filters_head_branch_main(workflow: dict) -> None:
    triage = workflow["jobs"]["triage"]
    cond = triage["if"]
    assert "workflow_run.head_branch == 'main'" in cond


def test_triage_job_blocks_canonical_repo_only(workflow: dict) -> None:
    triage = workflow["jobs"]["triage"]
    cond = triage["if"]
    assert "head_repository.full_name == github.repository" in cond


def test_triage_job_recursion_guard_present(workflow: dict) -> None:
    triage = workflow["jobs"]["triage"]
    cond = triage["if"]
    assert "fix(ci-heal):" in cond, "Recursion guard on auto-heal commit-prefix missing"


def test_heal_job_permissions_no_actions_write(workflow: dict) -> None:
    heal = workflow["jobs"]["heal"]
    perms = heal.get("permissions", {})
    # actions:write would let the opened PR mutate workflows; that's the
    # recursion vector we're explicitly disallowing.
    assert "actions" not in perms or perms.get("actions") != "write", perms


def test_heal_job_permissions_minimum(workflow: dict) -> None:
    heal = workflow["jobs"]["heal"]
    perms = heal.get("permissions", {})
    assert perms.get("contents") == "write"
    assert perms.get("pull-requests") == "write"


def test_triage_job_permissions_read_only(workflow: dict) -> None:
    triage = workflow["jobs"]["triage"]
    perms = triage.get("permissions", {})
    for key, level in perms.items():
        assert level == "read", f"triage.{key} should be read, got {level}"


def test_concurrency_per_sha(workflow: dict) -> None:
    conc = workflow.get("concurrency", {})
    # Distinct prefix from bernstein-ci-fix.yml (``auto-heal-...``) so the
    # two workflows do not cancel each other.
    assert "ci-heal-" in conc.get("group", ""), conc
    assert conc.get("cancel-in-progress") is True


# ---- cordon allowlist behavioural test --------------------------------------


def _cordon_regex_from_workflow() -> re.Pattern[str]:
    text = _WF.read_text()
    match = re.search(r"ALLOW_RE='([^']+)'", text)
    assert match is not None, "ALLOW_RE not found in workflow"
    return re.compile(match.group(1))


@pytest.mark.parametrize(
    "path",
    [
        "typos.toml",
        ".typos.toml",
        "AGENTS.md",
        "CLAUDE.md",
        ".goosehints",
        "CONVENTIONS.md",
        ".cursor/rules/module-map.mdc",
        ".cursor/rules/documentation-duty.mdc",
    ],
)
def test_cordon_allowlist_matches_expected_paths(path: str) -> None:
    pattern = _cordon_regex_from_workflow()
    assert pattern.match(path), f"{path!r} should be in cordon allowlist"


@pytest.mark.parametrize(
    "path",
    [
        "src/bernstein/cli/main.py",
        "src/bernstein/core/orchestrator.py",
        "tests/unit/test_foo.py",
        "scripts/regen_contract_drift.py",
        "pyproject.toml",
        ".github/workflows/ci.yml",
        "README.md",
        "CHANGELOG.md",
    ],
)
def test_cordon_allowlist_rejects_unexpected_paths(path: str) -> None:
    pattern = _cordon_regex_from_workflow()
    assert pattern.match(path) is None, f"{path!r} should NOT be in cordon allowlist"


# ---- script references ------------------------------------------------------


def test_workflow_references_categorize_script() -> None:
    text = _WF.read_text()
    assert "scripts/auto_heal_categorize.py" in text


def test_workflow_references_typos_extract_script() -> None:
    text = _WF.read_text()
    assert "scripts/auto_heal_typos.py" in text


def test_workflow_references_typos_apply_script() -> None:
    text = _WF.read_text()
    assert "scripts/auto_heal_apply_typos.py" in text


def test_helper_scripts_exist() -> None:
    for name in (
        "auto_heal_categorize.py",
        "auto_heal_typos.py",
        "auto_heal_apply_typos.py",
        "auto_heal_recurrence.py",
    ):
        path = _REPO / "scripts" / name
        assert path.exists(), f"missing helper script: {name}"


def test_workflow_references_recurrence_script() -> None:
    text = _WF.read_text()
    assert "scripts/auto_heal_recurrence.py" in text


# ---- pinned-SHA hygiene -----------------------------------------------------


_SHA_RE = re.compile(r"uses:\s+\S+@([0-9a-fA-F]{40})\s")


def test_all_action_refs_pinned_to_40_char_sha() -> None:
    text = _WF.read_text()
    for line in text.splitlines():
        if "uses:" in line and "@" in line:
            stripped = line.strip()
            # Allow internal/self references like ``uses: ./`` if any.
            if stripped.startswith("- uses: ./") or stripped.startswith("uses: ./"):
                continue
            assert _SHA_RE.search(line + " "), f"unpinned action ref: {line}"


def test_no_persist_credentials_true() -> None:
    text = _WF.read_text()
    # We don't want any "persist-credentials: true". The explicit
    # ``persist-credentials: false`` is OK; absence + checkout-with-token
    # is the supported pattern.
    assert "persist-credentials: true" not in text
