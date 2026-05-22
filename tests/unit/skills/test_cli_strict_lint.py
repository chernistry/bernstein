"""CLI tests for strict skill lint gates (#1720)."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.skills_cmd import skills_group
from bernstein.core.skills.lifecycle import SKILLS_LOCK_FILENAME, SKILLS_TOML_FILENAME


def _write_broken_skill(path: Path) -> None:
    """Write a skill that installs by default but emits an ERROR lint finding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            """
            ---
            description: Missing the required skill name so lint reports an error.
            ---

            # Broken skill
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_skills_install_strict_blocks_error_findings_but_default_installs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "sources" / "broken.md"
    _write_broken_skill(source)
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    default_result = runner.invoke(skills_group, ["install", str(source)])
    installed = workdir / ".bernstein" / "skills" / "broken"
    assert default_result.exit_code == 0
    assert installed.is_dir()
    shutil.rmtree(installed)

    strict_result = runner.invoke(skills_group, ["install", str(source), "--strict"])
    assert strict_result.exit_code == 1
    assert not installed.exists()


def test_skills_sync_strict_blocks_error_findings_but_default_syncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = workdir / "sources" / "broken.md"
    _write_broken_skill(source)
    (workdir / SKILLS_TOML_FILENAME).write_text(
        '[[skills]]\nname = "broken"\nsource = "local"\npath = "./sources/broken.md"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    runner = CliRunner()

    default_result = runner.invoke(skills_group, ["sync"])
    installed = workdir / ".bernstein" / "skills" / "broken"
    assert default_result.exit_code == 0
    assert installed.is_dir()
    shutil.rmtree(installed)
    (workdir / SKILLS_LOCK_FILENAME).unlink()

    strict_result = runner.invoke(skills_group, ["sync", "--strict"])
    assert strict_result.exit_code == 1
    assert not installed.exists()
