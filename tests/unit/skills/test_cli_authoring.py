"""CLI tests for deterministic skill authoring tools (#1720)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from bernstein.cli.commands.skills_cmd import skills_group


def _write_skill(
    root: Path,
    name: str,
    *,
    keywords: tuple[str, ...],
    body: str = "# Skill\n\nUse this skill for deterministic tests.\n",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    frontmatter = yaml.safe_dump(
        {
            "manifest_schema": 1,
            "name": name,
            "description": f"Skill {name} used by deterministic authoring command tests.",
            "trigger_keywords": list(keywords),
            "references": [],
            "scripts": [],
            "assets": [],
        },
        sort_keys=False,
    )
    skill_dir.joinpath("SKILL.md").write_text(
        "---\n" + frontmatter + "---\n" + body,
        encoding="utf-8",
    )
    return skill_dir


def test_skills_test_runs_trigger_suite_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    skills_root = workdir / ".bernstein" / "skills"
    workdir.mkdir()
    _write_skill(skills_root, "pytest-helper", keywords=("pytest", "regression"))
    _write_skill(skills_root, "docs-helper", keywords=("markdown", "docs"))
    suite = workdir / "skill-triggers.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            cases:
              - name: pytest task
                query: Fix the pytest regression in the task router.
                expect:
                  include:
                    - pytest-helper
                  exclude:
                    - docs-helper
            """
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["test", str(suite)])

    assert result.exit_code == 0
    assert "1 passed" in result.output


def test_skills_test_fails_when_expected_skill_is_not_matched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    skills_root = workdir / ".bernstein" / "skills"
    workdir.mkdir()
    _write_skill(skills_root, "pytest-helper", keywords=("pytest",))
    suite = workdir / "skill-triggers.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            cases:
              - name: docs task
                query: Update the README.
                expect:
                  include:
                    - pytest-helper
            """
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["test", str(suite)])

    assert result.exit_code == 1
    assert "docs task" in result.output
    assert "missing: pytest-helper" in result.output


def test_skills_diff_reports_structural_changes(tmp_path: Path) -> None:
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left = _write_skill(left_root, "pytest-helper", keywords=("pytest",))
    right = _write_skill(
        right_root,
        "pytest-helper",
        keywords=("pytest", "regression"),
        body="# Skill\n\nUse this skill for regression tests.\n",
    )
    runner = CliRunner()

    result = runner.invoke(skills_group, ["diff", str(left), str(right)])

    assert result.exit_code == 1
    assert "changed" in result.output
    assert "manifest" in result.output
    assert "body" in result.output


def test_skills_bench_requires_explicit_suite_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    skills_root = workdir / ".bernstein" / "skills"
    workdir.mkdir()
    _write_skill(skills_root, "pytest-helper", keywords=("pytest",))
    suite = workdir / "skill-triggers.yaml"
    suite.write_text(
        textwrap.dedent(
            """
            cases:
              - name: pytest task
                query: Fix the pytest regression.
                expect:
                  include:
                    - pytest-helper
            """
        ).lstrip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    missing_suite = runner.invoke(skills_group, ["bench"])
    explicit_suite = runner.invoke(skills_group, ["bench", str(suite), "--iterations", "2"])

    assert missing_suite.exit_code != 0
    assert explicit_suite.exit_code == 0
    assert "2 iteration(s)" in explicit_suite.output
