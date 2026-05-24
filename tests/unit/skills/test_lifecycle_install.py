"""Install / remove round-trip tests for the lifecycle module (#1720)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bernstein.core.skills.lifecycle import (
    InstallScope,
    SkillLifecycleError,
    init_skill,
    install_local,
    remove_skill,
    scope_root,
)
from bernstein.core.skills.manifest import parse_skill_md


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


def test_init_skill_creates_deterministic_project_scaffold(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()

    result = init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir)

    install_dir = scope_root(InstallScope.PROJECT, workdir=workdir) / "sample-skill"
    assert result.install_dir == install_dir
    assert (install_dir / "references").is_dir()
    assert (install_dir / "scripts").is_dir()
    assert (install_dir / "assets").is_dir()
    assert (install_dir / "SKILL.md").read_text(encoding="utf-8") == textwrap.dedent(
        """\
        ---
        manifest_schema: 1
        name: sample-skill
        description: Skill sample-skill scaffolded for deterministic authoring.
        trigger_keywords: []
        references: []
        scripts: []
        assets: []
        ---

        # Sample skill

        Describe when to use this skill and the exact workflow it should follow.
        """
    )


def test_init_skill_rejects_invalid_name(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()

    with pytest.raises(SkillLifecycleError, match="must match regex"):
        init_skill("Bad Name", scope=InstallScope.PROJECT, workdir=workdir)


def test_init_skill_rejects_invalid_description(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()

    with pytest.raises(SkillLifecycleError, match="invalid scaffold manifest"):
        init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir, description="short")


def test_init_skill_rejects_empty_description(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()

    with pytest.raises(SkillLifecycleError, match="invalid scaffold manifest"):
        init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir, description="")


def test_init_skill_wraps_scaffold_write_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()

    def fail_write_text(
        self: Path,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_write_text)

    with pytest.raises(SkillLifecycleError, match="failed to initialize scaffold"):
        init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir)


def test_init_skill_quotes_yaml_sensitive_description(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    description = "Use when: local deterministic authoring is needed."

    result = init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir, description=description)

    manifest, _body = parse_skill_md(result.install_dir / "SKILL.md")
    assert manifest.description == description


def test_init_skill_does_not_overwrite_existing_skill(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir)

    with pytest.raises(SkillLifecycleError, match="already exists"):
        init_skill("sample-skill", scope=InstallScope.PROJECT, workdir=workdir)


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


def test_install_local_rejects_invisible_unicode_by_default(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "poisoned-skill.md"
    source.write_text(
        textwrap.dedent(
            """
            ---
            name: poisoned-skill
            description: Valid skill containing an invisible instruction marker.
            ---

            # Poisoned skill

            Body before tag marker.
            """
        ).strip()
        + "\U000e0048\n",
        encoding="utf-8",
    )

    install_dir = scope_root(InstallScope.PROJECT, workdir=workdir) / "poisoned-skill"
    with pytest.raises(SkillLifecycleError, match="invisible Unicode"):
        install_local(source, scope=InstallScope.PROJECT, workdir=workdir)
    assert not install_dir.exists()


def test_install_local_wraps_invalid_utf8_for_sanitizer_gate(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "bad-skill"
    source.mkdir()
    (source / "SKILL.md").write_bytes(
        b"---\nname: bad-skill\ndescription: Invalid bytes.\n---\n\n# Bad skill\n\xff\n",
    )

    install_dir = scope_root(InstallScope.PROJECT, workdir=workdir) / "bad-skill"
    with pytest.raises(SkillLifecycleError, match="cannot read SKILL.md for sanitizer gate"):
        install_local(source, scope=InstallScope.PROJECT, workdir=workdir)
    assert not install_dir.exists()


def test_install_local_accepts_invisible_unicode_when_explicitly_allowed(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "poisoned-skill.md"
    source.write_text(
        textwrap.dedent(
            """
            ---
            name: poisoned-skill
            description: Valid skill containing an invisible instruction marker.
            ---

            # Poisoned skill
            """
        ).strip()
        + "\n\U000e0048\n",
        encoding="utf-8",
    )

    result = install_local(
        source,
        scope=InstallScope.PROJECT,
        workdir=workdir,
        allow_invisible_unicode=True,
    )

    assert "\U000e0048" in (result.install_dir / "SKILL.md").read_text(encoding="utf-8")


def test_install_local_rejects_sandbox_profile_without_accept_risk(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    source = tmp_path / "sandboxed-skill.md"
    source.write_text(
        textwrap.dedent(
            """
            ---
            name: sandboxed-skill
            description: Valid skill declaring a sandbox profile for future injector support.
            sandbox_profile: read-only
            ---

            # Sandboxed skill
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    install_dir = scope_root(InstallScope.PROJECT, workdir=workdir) / "sandboxed-skill"
    with pytest.raises(SkillLifecycleError, match="sandbox_profile"):
        install_local(source, scope=InstallScope.PROJECT, workdir=workdir)
    assert not install_dir.exists()

    result = install_local(source, scope=InstallScope.PROJECT, workdir=workdir, accept_risk=True)
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
