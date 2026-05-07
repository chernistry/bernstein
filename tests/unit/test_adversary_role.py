"""Tests for the adversary reviewer role template and registry wiring.

Covers:
    * The role's templates/roles/adversary/ directory exists with the
      three expected files (system_prompt.md, task_prompt.md, config.yaml).
    * The role appears in the canonical KNOWN_ROLES registry and the
      plan_validate fallback set.
    * The role is discoverable by the role-resolver legacy path.
    * The system prompt enforces the structured-finding contract used
      by the downstream merge gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bernstein.cli.commands.plan_validate_cmd import _KNOWN_ROLES
from bernstein.core.planning.plan_schema import KNOWN_ROLES
from bernstein.core.planning.role_resolver import (
    invalidate_cache,
    resolve_role_prompt,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ADVERSARY_DIR = _REPO_ROOT / "templates" / "roles" / "adversary"


@pytest.fixture(autouse=True)
def _clear_resolver_cache() -> None:
    """Reset the role-resolver cache between tests."""
    invalidate_cache()


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------


class TestAdversaryRoleFilesExist:
    """The role lives under templates/roles/adversary/ with the standard files."""

    def test_role_directory_exists(self) -> None:
        assert _ADVERSARY_DIR.is_dir(), (
            f"Expected {_ADVERSARY_DIR} to exist as a directory"
        )

    def test_system_prompt_exists_and_non_empty(self) -> None:
        system_prompt = _ADVERSARY_DIR / "system_prompt.md"
        assert system_prompt.is_file()
        assert len(system_prompt.read_text(encoding="utf-8")) > 200

    def test_task_prompt_exists_and_non_empty(self) -> None:
        task_prompt = _ADVERSARY_DIR / "task_prompt.md"
        assert task_prompt.is_file()
        assert len(task_prompt.read_text(encoding="utf-8")) > 200

    def test_config_yaml_exists_and_parses(self) -> None:
        config = _ADVERSARY_DIR / "config.yaml"
        assert config.is_file()
        parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)
        assert "default_model" in parsed
        assert "default_effort" in parsed
        assert "max_tasks_per_session" in parsed
        # Adversary runs as the last gate before merge — keep its
        # session short so it cannot drift onto unrelated tickets.
        assert parsed["max_tasks_per_session"] == 1


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


class TestAdversaryInRegistries:
    """The adversary role appears in every place the orchestrator consults."""

    def test_in_canonical_known_roles(self) -> None:
        assert "adversary" in KNOWN_ROLES

    def test_in_plan_validate_fallback(self) -> None:
        assert "adversary" in _KNOWN_ROLES

    def test_known_roles_remain_sorted(self) -> None:
        # The canonical list is alphabetically sorted; if a future edit
        # breaks this we want to know.
        assert sorted(KNOWN_ROLES) == KNOWN_ROLES


# ---------------------------------------------------------------------------
# Role-resolver discovery (legacy path)
# ---------------------------------------------------------------------------


class TestAdversaryDiscoverable:
    """The role-resolver finds the adversary template via the legacy path."""

    def test_resolver_finds_legacy_template(self) -> None:
        roles_dir = _REPO_ROOT / "templates" / "roles"
        resolved = resolve_role_prompt(
            "adversary",
            templates_dir=roles_dir,
            include_plugins=False,
        )
        # No skill pack exists for adversary; fall back to the legacy
        # template.
        assert resolved.source in {"skill", "legacy"}
        assert resolved.body  # non-empty


# ---------------------------------------------------------------------------
# Output-format contract
# ---------------------------------------------------------------------------


class TestAdversaryPromptContract:
    """The system prompt must pin the JSON output shape used by the gate."""

    def test_system_prompt_documents_severity_levels(self) -> None:
        body = (_ADVERSARY_DIR / "system_prompt.md").read_text(encoding="utf-8")
        for severity in ("info", "warning", "critical"):
            assert severity in body, f"system prompt missing severity: {severity}"

    def test_system_prompt_pins_falsification_test_field(self) -> None:
        body = (_ADVERSARY_DIR / "system_prompt.md").read_text(encoding="utf-8")
        # The merge gate reads `falsification_test` to know how to close
        # findings; the prompt must spell it out.
        assert "falsification_test" in body

    def test_system_prompt_pins_findings_array(self) -> None:
        body = (_ADVERSARY_DIR / "system_prompt.md").read_text(encoding="utf-8")
        assert '"findings"' in body or "findings" in body

    def test_system_prompt_forbids_modifying_source(self) -> None:
        body = (_ADVERSARY_DIR / "system_prompt.md").read_text(encoding="utf-8")
        # The role is read-only; it must say so explicitly.
        lowered = body.lower()
        assert "do not modify" in lowered or "do not approve" in lowered

    def test_task_prompt_has_task_description_placeholder(self) -> None:
        body = (_ADVERSARY_DIR / "task_prompt.md").read_text(encoding="utf-8")
        assert "{{TASK_DESCRIPTION}}" in body
        assert "{{TASK_TITLE}}" in body
