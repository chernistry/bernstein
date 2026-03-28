"""Tests for AgencyProvider — parses msitarzewski/agency-agents format."""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from bernstein.agents.agency_provider import AgencyProvider
from bernstein.agents.catalog import CatalogAgent

# ---------------------------------------------------------------------------
# Sample Agency markdown files
# ---------------------------------------------------------------------------

FULL_AGENT_MD = textwrap.dedent("""\
    ---
    name: Code Reviewer
    description: Expert code reviewer focused on correctness and security.
    color: purple
    emoji: "\U0001f441\ufe0f"
    vibe: Reviews code like a mentor, not a gatekeeper.
    ---

    # Code Reviewer Agent

    You are **Code Reviewer**, an expert who provides thorough code reviews.
""")

MINIMAL_AGENT_MD = textwrap.dedent("""\
    ---
    name: Minimal Agent
    ---

    Just the basics.
""")

NO_FRONTMATTER_MD = "# No frontmatter here\n\nJust a body."

EMPTY_NAME_MD = textwrap.dedent("""\
    ---
    name: ""
    description: No name given
    ---

    Some content.
""")


# ---------------------------------------------------------------------------
# _parse_file
# ---------------------------------------------------------------------------


class TestParseFile:
    def test_parses_full_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "engineering-code-reviewer.md"
        f.write_text(FULL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert len(agents) == 1
        agent = agents[0]
        assert agent.name == "Code Reviewer"
        assert agent.description == "Expert code reviewer focused on correctness and security."
        assert "Code Reviewer" in agent.system_prompt

    def test_id_is_slugified_name_with_prefix(self, tmp_path: Path) -> None:
        f = tmp_path / "engineering-code-reviewer.md"
        f.write_text(FULL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert agents[0].id == "agency:code-reviewer"

    def test_source_is_agency(self, tmp_path: Path) -> None:
        f = tmp_path / "engineering-code-reviewer.md"
        f.write_text(FULL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert agents[0].source == "agency"

    def test_engineering_maps_to_backend_role(self, tmp_path: Path) -> None:
        f = tmp_path / "engineering-code-reviewer.md"
        f.write_text(FULL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert agents[0].role == "backend"

    def test_design_maps_to_architect_role(self, tmp_path: Path) -> None:
        f = tmp_path / "design-ui-specialist.md"
        f.write_text(MINIMAL_AGENT_MD.replace("Minimal Agent", "UI Specialist"))
        agents = AgencyProvider._parse_file(f, division="design")
        assert agents[0].role == "architect"

    def test_unknown_division_kept_as_is(self, tmp_path: Path) -> None:
        f = tmp_path / "xr-spatial-agent.md"
        f.write_text(MINIMAL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="xr")
        assert agents[0].role == "xr"

    def test_tools_is_empty_list(self, tmp_path: Path) -> None:
        f = tmp_path / "engineering-code-reviewer.md"
        f.write_text(FULL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert agents[0].tools == []

    def test_parses_minimal_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "general-minimal.md"
        f.write_text(MINIMAL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="general")
        assert len(agents) == 1
        assert agents[0].name == "Minimal Agent"
        assert agents[0].description == ""

    def test_skips_file_without_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text(NO_FRONTMATTER_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert agents == []

    def test_skips_file_with_empty_name(self, tmp_path: Path) -> None:
        f = tmp_path / "empty-name.md"
        f.write_text(EMPTY_NAME_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert agents == []

    def test_returns_catalog_agent_instances(self, tmp_path: Path) -> None:
        f = tmp_path / "engineering-code-reviewer.md"
        f.write_text(FULL_AGENT_MD)
        agents = AgencyProvider._parse_file(f, division="engineering")
        assert all(isinstance(a, CatalogAgent) for a in agents)


# ---------------------------------------------------------------------------
# provider_id / is_available
# ---------------------------------------------------------------------------


class TestProviderMeta:
    def test_provider_id(self, tmp_path: Path) -> None:
        provider = AgencyProvider(local_path=tmp_path)
        assert provider.provider_id() == "agency"

    def test_is_available_when_dir_exists(self, tmp_path: Path) -> None:
        provider = AgencyProvider(local_path=tmp_path)
        assert provider.is_available() is True

    def test_is_not_available_when_dir_missing(self, tmp_path: Path) -> None:
        provider = AgencyProvider(local_path=tmp_path / "nonexistent")
        assert provider.is_available() is False


# ---------------------------------------------------------------------------
# fetch_agents
# ---------------------------------------------------------------------------


class TestFetchAgents:
    def test_returns_empty_list_for_empty_dir(self, tmp_path: Path) -> None:
        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.fetch_agents())
        assert agents == []

    def test_scans_subdirectories_for_md_files(self, tmp_path: Path) -> None:
        eng = tmp_path / "engineering"
        eng.mkdir()
        (eng / "engineering-code-reviewer.md").write_text(FULL_AGENT_MD)

        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.fetch_agents())
        assert len(agents) == 1

    def test_skips_non_md_files(self, tmp_path: Path) -> None:
        eng = tmp_path / "engineering"
        eng.mkdir()
        (eng / "notes.txt").write_text("ignore me")
        (eng / "engineering-code-reviewer.md").write_text(FULL_AGENT_MD)

        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.fetch_agents())
        assert len(agents) == 1

    def test_loads_agents_from_multiple_divisions(self, tmp_path: Path) -> None:
        (tmp_path / "engineering").mkdir()
        (tmp_path / "design").mkdir()
        (tmp_path / "engineering" / "engineering-code-reviewer.md").write_text(FULL_AGENT_MD)
        (tmp_path / "design" / "design-ui.md").write_text(MINIMAL_AGENT_MD)

        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.fetch_agents())
        assert len(agents) == 2

    def test_agent_id_uses_agency_prefix(self, tmp_path: Path) -> None:
        eng = tmp_path / "engineering"
        eng.mkdir()
        (eng / "engineering-code-reviewer.md").write_text(FULL_AGENT_MD)

        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.fetch_agents())
        assert agents[0].id == "agency:code-reviewer"

    def test_division_name_derived_from_directory(self, tmp_path: Path) -> None:
        qa = tmp_path / "qa_testing"
        qa.mkdir()
        (qa / "qa_testing-tester.md").write_text(MINIMAL_AGENT_MD)

        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.fetch_agents())
        assert agents[0].role == "qa"


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_returns_agents_like_fetch(self, tmp_path: Path) -> None:
        eng = tmp_path / "engineering"
        eng.mkdir()
        (eng / "engineering-code-reviewer.md").write_text(FULL_AGENT_MD)

        provider = AgencyProvider(local_path=tmp_path)
        agents = asyncio.run(provider.refresh())
        assert len(agents) == 1

    def test_refresh_picks_up_new_files(self, tmp_path: Path) -> None:
        eng = tmp_path / "engineering"
        eng.mkdir()

        provider = AgencyProvider(local_path=tmp_path)
        assert asyncio.run(provider.fetch_agents()) == []

        (eng / "engineering-code-reviewer.md").write_text(FULL_AGENT_MD)
        agents = asyncio.run(provider.refresh())
        assert len(agents) == 1
