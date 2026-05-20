"""Smoke tests for ``bernstein spec`` CLI subcommands (issue #1631)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.spec_cmd import spec_group

CLEAN_SPEC = """# Add cohort export

## Acceptance criteria
- New endpoint returns CSV.

## Out of scope
- nothing else

## Tested via
- pytest tests/unit/api/test_cohort_export.py
"""

MISSING_AC_SPEC = """# Spec

## Out of scope
- nothing

## Tested via
- pytest
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_spec_check_passes_on_clean_spec(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(CLEAN_SPEC)
    result = runner.invoke(spec_group, ["check", str(spec), "--workspace-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "acceptance_criteria_present" in result.output


def test_spec_check_fails_on_dirty_spec(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(MISSING_AC_SPEC)
    result = runner.invoke(spec_group, ["check", str(spec), "--workspace-root", str(tmp_path)])
    assert result.exit_code == 2
    assert "acceptance_criteria_present" in result.output


def test_spec_check_no_strict_returns_zero(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(MISSING_AC_SPEC)
    result = runner.invoke(
        spec_group,
        ["check", str(spec), "--workspace-root", str(tmp_path), "--no-strict"],
    )
    assert result.exit_code == 0


def test_spec_auto_fix_dry_run_does_not_persist(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(MISSING_AC_SPEC)
    result = runner.invoke(
        spec_group,
        ["auto-fix", str(spec), "--workspace-root", str(tmp_path), "--max-iter", "3"],
    )
    # Heuristic patcher adds a stub Acceptance criteria section, so the gate
    # converges and exits 0; the file on disk must still be untouched.
    assert result.exit_code == 0, result.output
    assert spec.read_text() == MISSING_AC_SPEC


def test_spec_auto_fix_write_persists_changes(runner: CliRunner, tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text(MISSING_AC_SPEC)
    result = runner.invoke(
        spec_group,
        [
            "auto-fix",
            str(spec),
            "--workspace-root",
            str(tmp_path),
            "--max-iter",
            "3",
            "--write",
        ],
    )
    assert result.exit_code == 0, result.output
    new_text = spec.read_text()
    assert "Acceptance criteria" in new_text
    assert new_text != MISSING_AC_SPEC
