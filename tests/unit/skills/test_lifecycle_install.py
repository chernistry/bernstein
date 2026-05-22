"""Install / remove round-trip tests for the lifecycle module (#1720)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bernstein.core.skills.lifecycle import (
    InstallScope,
    SkillLifecycleError,
    install_local,
    remove_skill,
    scope_root,
)


@pytest.fixture
def single_file_skill(tmp_path: Path) -> Path:
    """A standalone SKILL.md, the shape used by the sample TOML entry."""
    source = tmp_path / "sample-skill.md"
    source.write_text(
        textwrap.dedent(
            """
            ---
            name: sample-skill
            description: Sample skill used by lifecycle tests to round trip install.
            ---

            # Sample skill

            Body content.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return source


@pytest.fixture
def directory_skill(tmp_path: Path) -> Path:
    """A directory-shaped skill with a referenced file."""
    skill_dir = tmp_path / "dir-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: dir-skill
            description: Directory-shaped skill exercising the references bucket too.
            references:
              - deep-dive.md
            ---

            # Dir skill
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "deep-dive.md").write_text("# Deep dive\n", encoding="utf-8")
    return skill_dir


def test_install_local_single_file_project_scope(
    tmp_path: Path,
    single_file_skill: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    result = install_local(
        single_file_skill,
        scope=InstallScope.PROJECT,
        workdir=workdir,
    )

    expected_dir = workdir / ".bernstein" / "skills" / "sample-skill"
    assert result.install_dir == expected_dir
    assert (expected_dir / "SKILL.md").is_file()
    assert result.digest.digest  # non-empty hex
    assert len(result.digest.digest) == 64


def test_install_local_directory_user_scope(
    tmp_path: Path,
    directory_skill: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    workdir = tmp_path / "project"
    workdir.mkdir()
    result = install_local(
        directory_skill,
        scope=InstallScope.USER,
        workdir=workdir,
        home=home,
    )

    expected_dir = home / ".bernstein" / "skills" / "dir-skill"
    assert result.install_dir == expected_dir
    assert (expected_dir / "SKILL.md").is_file()
    assert (expected_dir / "references" / "deep-dive.md").is_file()


def test_remove_skill_round_trip(
    tmp_path: Path,
    single_file_skill: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    install_local(single_file_skill, scope=InstallScope.PROJECT, workdir=workdir)
    install_dir = scope_root(InstallScope.PROJECT, workdir=workdir) / "sample-skill"
    assert install_dir.is_dir()

    assert remove_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir) is True
    assert not install_dir.exists()
    # Second remove is a no-op.
    assert remove_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir) is False


def test_install_local_rejects_missing_source(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    with pytest.raises(SkillLifecycleError, match="does not exist"):
        install_local(
            tmp_path / "missing.md",
            scope=InstallScope.PROJECT,
            workdir=workdir,
        )


def test_install_local_rejects_non_md_file(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    bad = tmp_path / "skill.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(SkillLifecycleError, match=".md extension"):
        install_local(bad, scope=InstallScope.PROJECT, workdir=workdir)


def test_install_local_directory_without_skill_md(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    src = tmp_path / "empty-skill"
    src.mkdir()
    with pytest.raises(SkillLifecycleError, match="does not contain SKILL.md"):
        install_local(src, scope=InstallScope.PROJECT, workdir=workdir)


def test_install_local_strict_lint_blocks_error_findings_but_default_installs(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "broken-skill.md"
    source.write_text(
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

    install_local(source, scope=InstallScope.PROJECT, workdir=workdir)
    installed = scope_root(InstallScope.PROJECT, workdir=workdir) / "broken-skill"
    assert installed.is_dir()
    remove_skill("broken-skill", scope=InstallScope.PROJECT, workdir=workdir)

    with pytest.raises(SkillLifecycleError, match="strict lint failed.*invalid-manifest"):
        install_local(source, scope=InstallScope.PROJECT, workdir=workdir, strict_lint=True)
    assert not installed.exists()


def test_install_local_strict_lint_allows_warning_findings(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "warning-skill.md"
    source.write_text(
        textwrap.dedent(
            """
            ---
            name: warning-skill
            description: Valid skill with an extra frontmatter key that lint reports as a warning.
            whenToUse: When the agent needs this skill.
            ---

            # Warning skill
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    result = install_local(source, scope=InstallScope.PROJECT, workdir=workdir, strict_lint=True)

    assert result.install_dir.is_dir()


def test_install_overwrites_previous(
    tmp_path: Path,
    single_file_skill: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    install_local(single_file_skill, scope=InstallScope.PROJECT, workdir=workdir)
    install_dir = scope_root(InstallScope.PROJECT, workdir=workdir) / "sample-skill"
    # Drop a stale file that should not survive re-install.
    stale = install_dir / "stale.txt"
    stale.write_text("stale", encoding="utf-8")
    assert stale.exists()

    install_local(single_file_skill, scope=InstallScope.PROJECT, workdir=workdir)
    assert not stale.exists()
