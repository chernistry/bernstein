"""Digest determinism tests for the lifecycle module (#1720)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from bernstein.core.skills.lifecycle import compute_skill_digest


def _author_skill(
    root: Path,
    name: str,
    *,
    body: str = "Body.",
    reference: str | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    front = [
        "---",
        f"name: {name}",
        "description: Sample skill used by digest determinism tests for round trips.",
    ]
    if reference:
        front += ["references:", f"  - {reference}"]
    front += ["---"]
    (skill_dir / "SKILL.md").write_text(
        "\n".join(front) + f"\n\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    if reference:
        refs = skill_dir / "references"
        refs.mkdir()
        (refs / reference).write_text("# ref body\n", encoding="utf-8")
    return skill_dir


def test_compute_skill_digest_is_deterministic(tmp_path: Path) -> None:
    a = _author_skill(tmp_path / "first", "alpha", body="Identical body.")
    b = _author_skill(tmp_path / "second", "alpha", body="Identical body.")
    assert compute_skill_digest(a).digest == compute_skill_digest(b).digest


def test_compute_skill_digest_detects_body_drift(tmp_path: Path) -> None:
    a = _author_skill(tmp_path / "first", "alpha", body="Original body.")
    b = _author_skill(tmp_path / "second", "alpha", body="Drifted body.")
    assert compute_skill_digest(a).digest != compute_skill_digest(b).digest


def test_compute_skill_digest_includes_referenced_files(tmp_path: Path) -> None:
    a = _author_skill(tmp_path / "first", "alpha", reference="ref.md")
    b = _author_skill(tmp_path / "second", "alpha", reference="ref.md")
    # Same setup -> same digest.
    assert compute_skill_digest(a).digest == compute_skill_digest(b).digest

    # Mutate the referenced file -> digest changes even though SKILL.md
    # is byte-identical.
    (b / "references" / "ref.md").write_text("# different ref\n", encoding="utf-8")
    assert compute_skill_digest(a).digest != compute_skill_digest(b).digest


def test_compute_skill_digest_ignores_undeclared_files(tmp_path: Path) -> None:
    a = _author_skill(tmp_path / "first", "alpha")
    b = _author_skill(tmp_path / "second", "alpha")
    # Add a scratch file that the manifest does not reference. It must not
    # leak into the digest.
    (b / "scratch.txt").write_text("not in manifest", encoding="utf-8")
    assert compute_skill_digest(a).digest == compute_skill_digest(b).digest


def test_canonical_frontmatter_key_order_does_not_change_digest(tmp_path: Path) -> None:
    """Re-ordering YAML keys must not invalidate the lock."""
    a = tmp_path / "ordered"
    a.mkdir()
    (a / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            name: ordered
            description: Frontmatter ordering should not affect the digest.
            ---

            # Ordered skill
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    b = tmp_path / "reordered"
    b.mkdir()
    (b / "SKILL.md").write_text(
        textwrap.dedent(
            """
            ---
            description: Frontmatter ordering should not affect the digest.
            name: ordered
            ---

            # Ordered skill
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    assert compute_skill_digest(a).digest == compute_skill_digest(b).digest
