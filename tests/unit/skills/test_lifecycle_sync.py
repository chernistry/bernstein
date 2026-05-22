"""Sync + lock determinism tests (#1720)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bernstein.core.skills.lifecycle import (
    SKILLS_LOCK_FILENAME,
    SKILLS_TOML_FILENAME,
    InstallScope,
    SkillLifecycleError,
    SkillsTomlError,
    read_lock_entries,
    scope_root,
    sync_skills,
)


def _write_source_md(path: Path, *, name: str, body: str = "Body.") -> None:
    """Author a tiny SKILL.md at *path* used as a `local` source."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""
            ---
            name: {name}
            description: Sample skill used by sync determinism regression tests.
            ---

            # {name}

            {body}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _write_manifest(workdir: Path, entries: list[tuple[str, str]]) -> Path:
    """Write a ``bernstein-skills.toml`` with one ``[[skills]]`` table per entry."""
    lines: list[str] = []
    for name, rel_path in entries:
        lines += [
            "[[skills]]",
            f'name = "{name}"',
            'source = "local"',
            f'path = "{rel_path}"',
            "",
        ]
    toml_path = workdir / SKILLS_TOML_FILENAME
    toml_path.write_text("\n".join(lines), encoding="utf-8")
    return toml_path


def test_sync_installs_declared_skills(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    _write_source_md(workdir / "sources" / "alpha.md", name="alpha")
    _write_source_md(workdir / "sources" / "beta.md", name="beta")
    toml_path = _write_manifest(
        workdir,
        [
            ("alpha", "./sources/alpha.md"),
            ("beta", "./sources/beta.md"),
        ],
    )

    outcomes = sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    install_root = scope_root(InstallScope.PROJECT, workdir=workdir)

    assert {o.name for o in outcomes} == {"alpha", "beta"}
    assert all(o.action == "installed" for o in outcomes)
    assert (install_root / "alpha" / "SKILL.md").is_file()
    assert (install_root / "beta" / "SKILL.md").is_file()
    assert (workdir / SKILLS_LOCK_FILENAME).is_file()


def test_sync_idempotent_lock_byte_identical(tmp_path: Path) -> None:
    """Second sync against unchanged sources rewrites an identical lock."""
    workdir = tmp_path / "project"
    workdir.mkdir()
    _write_source_md(workdir / "sources" / "alpha.md", name="alpha")
    toml_path = _write_manifest(workdir, [("alpha", "./sources/alpha.md")])

    sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    first_lock = (workdir / SKILLS_LOCK_FILENAME).read_bytes()

    outcomes = sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    second_lock = (workdir / SKILLS_LOCK_FILENAME).read_bytes()

    assert first_lock == second_lock
    assert outcomes[0].action == "unchanged"


def test_sync_detects_source_drift_and_rewrites_lock(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    src = workdir / "sources" / "alpha.md"
    _write_source_md(src, name="alpha", body="Original body.")
    toml_path = _write_manifest(workdir, [("alpha", "./sources/alpha.md")])

    sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    entries_before = read_lock_entries(workdir)
    assert len(entries_before) == 1
    digest_before = entries_before[0].digest

    # Mutate the source - the digest must change and the action must become
    # ``updated`` so a follow-up sync notices.
    _write_source_md(src, name="alpha", body="Drifted body.")
    outcomes = sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    entries_after = read_lock_entries(workdir)

    assert outcomes[0].action == "updated"
    assert entries_after[0].digest != digest_before


def test_sync_rejects_unknown_source_type(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    toml = workdir / SKILLS_TOML_FILENAME
    toml.write_text(
        '[[skills]]\nname = "alpha"\nsource = "git"\npath = "./alpha"\n',
        encoding="utf-8",
    )
    with pytest.raises(SkillsTomlError, match="only.*supported"):
        sync_skills(toml, scope=InstallScope.PROJECT, workdir=workdir)


def test_sync_handles_empty_manifest(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    toml = workdir / SKILLS_TOML_FILENAME
    toml.write_text("", encoding="utf-8")
    assert sync_skills(toml, scope=InstallScope.PROJECT, workdir=workdir) == []
    # The lock file is still written (deterministically empty).
    assert (workdir / SKILLS_LOCK_FILENAME).is_file()


def test_sync_directory_source_round_trips_referenced_files(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    src_dir = workdir / "sources" / "gamma"
    src_dir.mkdir(parents=True)
    (src_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: gamma
            description: Directory source covering reference round trip in sync.
            references:
              - notes.md
            ---

            # Gamma
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    refs = src_dir / "references"
    refs.mkdir()
    (refs / "notes.md").write_text("# Notes\n", encoding="utf-8")

    toml_path = _write_manifest(workdir, [("gamma", "./sources/gamma")])
    sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)

    installed_ref = scope_root(InstallScope.PROJECT, workdir=workdir) / "gamma" / "references" / "notes.md"
    assert installed_ref.is_file()


def test_sync_strict_lint_blocks_error_findings_but_default_syncs(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    broken = workdir / "sources" / "broken.md"
    broken.parent.mkdir(parents=True)
    broken.write_text(
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
    toml_path = _write_manifest(workdir, [("broken", "./sources/broken.md")])

    outcomes = sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    installed = scope_root(InstallScope.PROJECT, workdir=workdir) / "broken"
    assert outcomes[0].action == "installed"
    assert installed.is_dir()
    remove = installed
    for child in remove.iterdir():
        child.unlink()
    remove.rmdir()
    (workdir / SKILLS_LOCK_FILENAME).unlink()

    with pytest.raises(SkillLifecycleError, match="strict lint failed.*invalid-manifest"):
        sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir, strict_lint=True)
    assert not installed.exists()


def test_sync_rejects_invisible_unicode_skill(tmp_path: Path) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    poisoned = workdir / "sources" / "poisoned.md"
    _write_source_md(poisoned, name="poisoned", body="Body with tag marker \U000e0048.")
    toml_path = _write_manifest(workdir, [("poisoned", "./sources/poisoned.md")])

    with pytest.raises(SkillLifecycleError, match="invisible Unicode"):
        sync_skills(toml_path, scope=InstallScope.PROJECT, workdir=workdir)
    assert not (scope_root(InstallScope.PROJECT, workdir=workdir) / "poisoned").exists()
