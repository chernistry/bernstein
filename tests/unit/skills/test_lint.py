"""Tests for advisory skill linting (#1720)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from bernstein.core.skills.lint import LintSeverity, lint_skill


def _author(skill_dir: Path, content: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_lint_passes_for_well_formed_skill(tmp_path: Path) -> None:
    _author(
        tmp_path / "good",
        textwrap.dedent(
            """
            ---
            name: good
            description: A well formed skill that satisfies every lint rule cleanly.
            ---

            # Good skill

            Body text.
            """
        ).strip()
        + "\n",
    )
    assert lint_skill(tmp_path / "good") == []


def test_lint_warns_on_extra_keys(tmp_path: Path) -> None:
    _author(
        tmp_path / "with-extra",
        textwrap.dedent(
            """
            ---
            name: with-extra
            description: Skill with a Claude Code shaped frontmatter extra key for ware.
            whenToUse: When the agent needs to do the thing.
            ---

            # With extra
            """
        ).strip()
        + "\n",
    )
    findings = lint_skill(tmp_path / "with-extra")
    codes = {(f.code, f.severity) for f in findings}
    assert ("extra-key", LintSeverity.WARNING) in codes
    # Strict schema is still satisfied because the extra key is pre-filtered.
    assert not any(f.severity is LintSeverity.ERROR for f in findings)


def test_lint_reports_invalid_frontmatter(tmp_path: Path) -> None:
    _author(
        tmp_path / "broken",
        textwrap.dedent(
            """
            ---
            description: missing a name field so this should fail validation outright.
            ---

            # Broken
            """
        ).strip()
        + "\n",
    )
    findings = lint_skill(tmp_path / "broken")
    assert any(f.code == "invalid-manifest" and f.severity is LintSeverity.ERROR for f in findings)


def test_lint_flags_missing_reference_file(tmp_path: Path) -> None:
    skill_dir = tmp_path / "missing-ref"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: missing-ref
            description: Declares a reference file that does not exist on disk for tests.
            references:
              - vanished.md
            ---

            # Missing ref
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    findings = lint_skill(skill_dir)
    assert any(f.code == "missing-reference" and "vanished.md" in f.message for f in findings)


def test_lint_detects_invisible_tag_codepoints(tmp_path: Path) -> None:
    # The literal characters here are U+E0048 etc., which the sanitiser
    # treats as a prompt-injection payload.
    poisoned_body = "\U000e0048\U000e0049 hidden"
    _author(
        tmp_path / "poisoned",
        textwrap.dedent(
            f"""
            ---
            name: poisoned
            description: Skill body carrying invisible Unicode codepoints to trip flag.
            ---

            # Poisoned

            {poisoned_body}
            """
        ).strip()
        + "\n",
    )
    findings = lint_skill(tmp_path / "poisoned")
    assert any(f.code == "sensitive-pattern" for f in findings)


def test_lint_warns_on_oversized_body(tmp_path: Path) -> None:
    big_body = "Line of text\n" * 800  # well past 5 KB
    content = (
        "---\n"
        "name: big\n"
        "description: Skill with an oversized body used to exercise the cap warn.\n"
        "---\n"
        "\n"
        "# Big skill\n"
        "\n"
        f"{big_body}"
    )
    _author(tmp_path / "big", content)
    findings = lint_skill(tmp_path / "big")
    assert any(f.code == "body-too-large" for f in findings)


def test_lint_reports_missing_skill_md(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    findings = lint_skill(empty)
    assert findings[0].code == "missing-skill-md"
    assert findings[0].severity is LintSeverity.ERROR
