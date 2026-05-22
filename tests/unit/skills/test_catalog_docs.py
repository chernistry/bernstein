"""Documentation contracts for the signed skill catalog."""

from __future__ import annotations

from pathlib import Path

from bernstein.core.skills.catalog.fetcher import DEFAULT_SKILLS_CATALOG_URL, DEFAULT_SKILLS_MIRROR_URL


def test_skills_catalog_docs_state_default_urls() -> None:
    """The documented catalog posture must match the shipped defaults."""
    docs = Path("docs/operations/skills-catalog.md").read_text(encoding="utf-8")
    normalized_docs = " ".join(docs.split())

    assert DEFAULT_SKILLS_CATALOG_URL in docs
    assert DEFAULT_SKILLS_MIRROR_URL in docs
    assert "No catalog network request is made until a `bernstein skills catalog` command runs." in normalized_docs
