"""Unit tests for ``bernstein.core.knowledge.agents_md_bridge``.

Covers the per-target render functions, the ``BridgeOutput`` shape, and
the round-trip equivalence promise from issue #1087: regardless of which
target a section is rewritten through, every section's *content* must
survive (we don't lose information when fanning out across targets).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.knowledge.agents_md_bridge import (
    ALL_TARGETS,
    BridgeOutput,
    render,
    render_all,
)
from bernstein.core.knowledge.agents_md_generator import AgentsMdSection

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_sections() -> list[AgentsMdSection]:
    """A minimal but non-trivial section list used by all targets."""
    return [
        AgentsMdSection(
            key="overview",
            title="Overview",
            body="Demo project for unit tests.",
            kind="overview",
            always_apply=True,
        ),
        AgentsMdSection(
            key="build-test",
            title="Build & test",
            body="```\nuv sync\nuv run pytest\n```",
            kind="build-test",
            always_apply=True,
        ),
        AgentsMdSection(
            key="module-map",
            title="Module map",
            body="- `core/` orchestration\n- `cli/` Click commands",
            kind="module-map",
            always_apply=False,
            target_globs=("src/**", "tests/**"),
        ),
    ]


# ---------------------------------------------------------------------------
# render() - target dispatch
# ---------------------------------------------------------------------------


class TestRenderDispatch:
    def test_unknown_target_raises(self, sample_sections: list[AgentsMdSection]) -> None:
        with pytest.raises(ValueError, match="Unknown render target"):
            render(sample_sections, "vscode", repo_name="demo")  # type: ignore[arg-type]

    def test_all_known_targets_dispatch(self, sample_sections: list[AgentsMdSection]) -> None:
        for t in ALL_TARGETS:
            out = render(sample_sections, t, repo_name="demo")
            assert isinstance(out, BridgeOutput)
            assert out.target == t
            assert out.files, f"target {t} produced no files"


# ---------------------------------------------------------------------------
# Canonical
# ---------------------------------------------------------------------------


class TestRenderCanonical:
    def test_writes_one_file_named_agents_md(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "canonical", repo_name="demo")
        assert list(out.files.keys()) == ["AGENTS.md"]

    def test_content_starts_with_h1(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "canonical", repo_name="demo")
        assert out.files["AGENTS.md"].startswith("# demo - AGENTS.md\n")


# ---------------------------------------------------------------------------
# Cursor - .cursor/rules/<key>.mdc with frontmatter
# ---------------------------------------------------------------------------


class TestRenderCursor:
    def test_emits_one_mdc_per_section(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "cursor", repo_name="demo")
        assert set(out.files.keys()) == {
            ".cursor/rules/overview.mdc",
            ".cursor/rules/build-test.mdc",
            ".cursor/rules/module-map.mdc",
        }

    def test_frontmatter_uses_real_cursor_fields(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "cursor", repo_name="demo")
        for content in out.files.values():
            assert content.startswith("---\n")
            assert "description:" in content
            assert "alwaysApply:" in content
            # YAML scalar separator after frontmatter, then blank line, then body.
            assert "\n---\n\n" in content

    def test_globs_emitted_only_when_present(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "cursor", repo_name="demo")
        overview = out.files[".cursor/rules/overview.mdc"]
        module_map = out.files[".cursor/rules/module-map.mdc"]
        assert "globs:" not in overview  # target_globs == ()
        assert "globs: src/**,tests/**" in module_map

    def test_always_apply_serialised_lowercase(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "cursor", repo_name="demo")
        overview = out.files[".cursor/rules/overview.mdc"]
        module_map = out.files[".cursor/rules/module-map.mdc"]
        assert "alwaysApply: true" in overview
        assert "alwaysApply: false" in module_map


# ---------------------------------------------------------------------------
# Claude Code - single CLAUDE.md
# ---------------------------------------------------------------------------


class TestRenderClaude:
    def test_single_file_at_root(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "claude", repo_name="demo")
        assert list(out.files.keys()) == ["CLAUDE.md"]

    def test_includes_every_section_heading(self, sample_sections: list[AgentsMdSection]) -> None:
        body = render(sample_sections, "claude", repo_name="demo").files["CLAUDE.md"]
        for sec in sample_sections:
            assert f"## {sec.title}" in body

    def test_no_yaml_frontmatter(self, sample_sections: list[AgentsMdSection]) -> None:
        body = render(sample_sections, "claude", repo_name="demo").files["CLAUDE.md"]
        # Frontmatter would mean starting with `---` immediately. We start
        # with `# {name} - CLAUDE.md`.
        assert not body.lstrip().startswith("---")


# ---------------------------------------------------------------------------
# Aider - CONVENTIONS.md + .aider.conf.yml
# ---------------------------------------------------------------------------


class TestRenderAider:
    def test_emits_two_files(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "aider", repo_name="demo")
        assert set(out.files.keys()) == {"CONVENTIONS.md", ".aider.conf.yml"}

    def test_conf_pins_read_to_conventions(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "aider", repo_name="demo")
        conf = out.files[".aider.conf.yml"]
        # Aider only loads CONVENTIONS.md when this line exists.
        assert "read: CONVENTIONS.md" in conf

    def test_conventions_includes_all_sections(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "aider", repo_name="demo")
        conv = out.files["CONVENTIONS.md"]
        for sec in sample_sections:
            assert sec.body.split("\n")[0] in conv


# ---------------------------------------------------------------------------
# Goose - .goosehints (plaintext)
# ---------------------------------------------------------------------------


class TestRenderGoose:
    def test_single_file_at_root(self, sample_sections: list[AgentsMdSection]) -> None:
        out = render(sample_sections, "goose", repo_name="demo")
        assert list(out.files.keys()) == [".goosehints"]

    def test_includes_section_titles(self, sample_sections: list[AgentsMdSection]) -> None:
        body = render(sample_sections, "goose", repo_name="demo").files[".goosehints"]
        for sec in sample_sections:
            assert sec.title in body


# ---------------------------------------------------------------------------
# Round-trip equivalence: every target's body retains every section's content
# ---------------------------------------------------------------------------


class TestRoundTripEquivalence:
    """Issue #1087 invariant: rendering through *any* target preserves the
    content payload of *every* canonical section.

    We don't claim byte-equivalence across targets - frontmatter, headings,
    and separators legitimately differ. We claim: for every section in the
    canonical IR, the substring of that section's first body-line appears
    in every target's emitted content.
    """

    def test_every_section_body_survives_every_target(self, sample_sections: list[AgentsMdSection]) -> None:
        outputs = render_all(sample_sections, repo_name="demo")
        for sec in sample_sections:
            # The first non-empty body line is the strongest cross-target
            # equivalence anchor. Headings are remapped by some targets;
            # body content is not.
            first_line = sec.body.splitlines()[0].strip()
            if not first_line or first_line.startswith("```"):
                # For fenced code blocks the strongest signal is the line
                # *after* the fence opener.
                lines = [l for l in sec.body.splitlines() if l.strip()]
                first_line = lines[1].strip() if len(lines) > 1 else lines[0].strip()
            for target, output in outputs.items():
                concat = "\n".join(output.files.values())
                assert first_line in concat, f"section {sec.key!r} body lost in target {target!r}"


# ---------------------------------------------------------------------------
# BridgeOutput.absolute_paths
# ---------------------------------------------------------------------------


class TestBridgeOutputAbsolutePaths:
    def test_resolves_relative_paths_against_root(self, tmp_path: Path) -> None:
        out = BridgeOutput(target="canonical", files={"AGENTS.md": "x"})
        abs_map = out.absolute_paths(tmp_path)
        assert list(abs_map.keys()) == [(tmp_path / "AGENTS.md").resolve()]
        assert abs_map[(tmp_path / "AGENTS.md").resolve()] == "x"
