"""CLI tests for deterministic skill scaffolding (#1720)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.skills_cmd import skills_group


def test_skills_init_creates_project_scaffold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["init", "sample-skill"])

    assert result.exit_code == 0
    assert "initialized sample-skill" in result.output
    assert (workdir / ".bernstein" / "skills" / "sample-skill" / "SKILL.md").is_file()
    assert (workdir / ".bernstein" / "skills" / "sample-skill" / "references").is_dir()
    assert (workdir / ".bernstein" / "skills" / "sample-skill" / "scripts").is_dir()
    assert (workdir / ".bernstein" / "skills" / "sample-skill" / "assets").is_dir()


def test_skills_init_surfaces_invalid_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    result = runner.invoke(skills_group, ["init", "Bad Name"])

    assert result.exit_code == 1
    assert "init failed" in result.output
    assert not (workdir / ".bernstein" / "skills" / "Bad Name").exists()
