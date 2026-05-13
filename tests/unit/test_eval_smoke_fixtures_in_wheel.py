"""Wheel-packaging regression: smoke fixtures must be loadable via
``importlib.resources`` exactly as a fresh ``pip install`` would see them.

This complements ``test_eval_smoke_fixtures.py``: that file exercises the
loader API end-to-end, while this file scopes a single, very narrow
assertion that catches a wheel-build regression even if the loader is
refactored.
"""

from __future__ import annotations

from importlib import resources


def test_packaged_smoke_resource_has_at_least_one_markdown_file() -> None:
    tier_root = resources.files("bernstein.eval.golden_data").joinpath("smoke")
    assert tier_root.is_dir(), (
        "bernstein.eval.golden_data.smoke missing as a packaged resource — "
        "check pyproject.toml [tool.hatch.build.targets.wheel].packages and "
        "the artifacts glob for src/bernstein/eval/golden_data/**/*.md"
    )
    md = [e for e in tier_root.iterdir() if e.name.endswith(".md")]
    assert md, "no smoke *.md fixtures shipped in the wheel"


def test_packaged_smoke_markdown_starts_with_yaml_frontmatter() -> None:
    """Cheap parse sanity check — every shipped fixture must have a YAML
    frontmatter block so the loader does not silently drop it.
    """
    tier_root = resources.files("bernstein.eval.golden_data").joinpath("smoke")
    md = [e for e in tier_root.iterdir() if e.name.endswith(".md")]
    assert md, "no smoke fixtures shipped — packaging regression"
    for entry in md:
        text = entry.read_text(encoding="utf-8")
        assert text.startswith("---"), f"packaged fixture {entry.name} missing YAML frontmatter"
        # Must close the frontmatter block.
        assert text.count("---") >= 2, f"packaged fixture {entry.name} has unterminated frontmatter"
