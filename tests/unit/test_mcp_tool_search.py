"""Tests for MCP tool-search lazy loading."""

from __future__ import annotations

import pytest

from bernstein.core.protocols.mcp.mcp_manager import build_tools_prompt_section
from bernstein.core.protocols.mcp.mcp_tool_search import (
    SearchHit,
    ToolCatalog,
    ToolEntry,
    ToolSearchEngine,
    build_prompt_section,
    compact_descriptions,
    estimate_tokens,
    expand_tools,
    search_tools,
    should_use_tool_search,
)


def _entry(name: str, summary: str, server: str = "git", schema: dict[str, object] | None = None) -> ToolEntry:
    return ToolEntry(name=name, summary=summary, server=server, schema=schema or {})


@pytest.fixture()
def small_catalog() -> ToolCatalog:
    return ToolCatalog(
        [
            _entry("git_diff", "show working tree changes", schema={"path": "string"}),
            _entry("git_commit", "create a commit from staged changes"),
            _entry("github_create_issue", "open a new GitHub issue", server="github"),
        ]
    )


@pytest.fixture()
def large_catalog() -> ToolCatalog:
    entries: list[ToolEntry] = [
        _entry(
            f"server{i}.tool_{i}",
            "do something useful with the input parameters and return a structured response payload",
            server=f"server{i}",
            schema={"a": "string", "b": "number", "c": "boolean", "d": {"nested": "object"}},
        )
        for i in range(80)
    ]
    return ToolCatalog(entries)


class TestToolEntry:
    def test_directory_line_truncates_long_summary(self) -> None:
        entry = _entry("a.tool", "x" * 500)
        line = entry.directory_line
        assert "…" in line
        assert len(line) < 500

    def test_directory_line_short_summary_unchanged(self) -> None:
        entry = _entry("a.tool", "short summary")
        assert entry.directory_line == "- a.tool (git): short summary"


class TestEstimateTokens:
    def test_empty_string_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_short_string_at_least_one(self) -> None:
        assert estimate_tokens("hi") >= 1

    def test_scales_with_length(self) -> None:
        short = estimate_tokens("a" * 4)
        long = estimate_tokens("a" * 400)
        assert long > short * 50


class TestToolCatalog:
    def test_len_and_contains(self, small_catalog: ToolCatalog) -> None:
        assert len(small_catalog) == 3
        assert "git_diff" in small_catalog
        assert "missing" not in small_catalog

    def test_expand_known_tools(self, small_catalog: ToolCatalog) -> None:
        out = small_catalog.expand(["git_diff", "git_commit"])
        assert set(out) == {"git_diff", "git_commit"}
        assert out["git_diff"]["input_schema"] == {"path": "string"}
        assert out["git_diff"]["server"] == "git"

    def test_expand_unknown_silently_skipped(self, small_catalog: ToolCatalog) -> None:
        out = small_catalog.expand(["git_diff", "nope"])
        assert set(out) == {"git_diff"}

    def test_full_tokens_grows_with_schema(self) -> None:
        thin = ToolCatalog([_entry("a", "b")])
        fat = ToolCatalog([_entry("a", "b", schema={f"k{i}": f"v{i}" for i in range(40)})])
        assert fat.estimate_full_tokens() > thin.estimate_full_tokens()


class TestSearchEngine:
    def test_ranking_prefers_query_term_matches(self, small_catalog: ToolCatalog) -> None:
        engine = ToolSearchEngine(small_catalog)
        hits = engine.search("git diff")
        assert hits, "expected at least one hit"
        assert hits[0].name == "git_diff"

    def test_substring_fallback(self, small_catalog: ToolCatalog) -> None:
        engine = ToolSearchEngine(small_catalog)
        hits = engine.search("github")
        assert any(h.name == "github_create_issue" for h in hits)

    def test_limit_respected(self) -> None:
        catalog = ToolCatalog([_entry(f"git_t{i}", "git tool") for i in range(20)])
        engine = ToolSearchEngine(catalog)
        hits = engine.search("git", limit=5)
        assert len(hits) == 5

    def test_empty_query_returns_no_bm25_hits(self, small_catalog: ToolCatalog) -> None:
        engine = ToolSearchEngine(small_catalog)
        assert engine.search("") == []

    def test_empty_catalog_returns_empty(self) -> None:
        engine = ToolSearchEngine(ToolCatalog([]))
        assert engine.search("anything") == []


class TestCompactDescriptions:
    def test_fits_within_budget(self, large_catalog: ToolCatalog) -> None:
        lines = compact_descriptions(large_catalog, budget_tokens=200)
        joined_tokens = sum(estimate_tokens(line) for line in lines)
        assert joined_tokens <= 200
        assert len(lines) < len(large_catalog)

    def test_zero_budget_returns_empty(self, small_catalog: ToolCatalog) -> None:
        assert compact_descriptions(small_catalog, budget_tokens=0) == []

    def test_huge_budget_returns_all(self, small_catalog: ToolCatalog) -> None:
        lines = compact_descriptions(small_catalog, budget_tokens=10_000)
        assert len(lines) == len(small_catalog)


class TestThreshold:
    def test_small_catalog_below_threshold(self, small_catalog: ToolCatalog) -> None:
        assert should_use_tool_search(small_catalog, threshold_tokens=6000) is False

    def test_large_catalog_above_threshold(self, large_catalog: ToolCatalog) -> None:
        assert should_use_tool_search(large_catalog, threshold_tokens=500) is True

    def test_zero_threshold_disables(self, large_catalog: ToolCatalog) -> None:
        assert should_use_tool_search(large_catalog, threshold_tokens=0) is False


class TestExpandRoundTrip:
    def test_search_then_expand(self, small_catalog: ToolCatalog) -> None:
        hits = search_tools(small_catalog, "git", limit=10)
        names = [h.name for h in hits]
        assert names
        schemas = expand_tools(small_catalog, names)
        assert set(schemas) == set(names)
        for name in names:
            assert "input_schema" in schemas[name]

    def test_expand_unknown_returns_empty(self, small_catalog: ToolCatalog) -> None:
        assert expand_tools(small_catalog, ["does-not-exist"]) == {}


class TestPromptSection:
    def test_inline_when_below_threshold(self, small_catalog: ToolCatalog) -> None:
        text = build_prompt_section(small_catalog, threshold_tokens=10_000)
        assert "MCP tools available" in text
        assert "tool_search" not in text

    def test_lazy_when_above_threshold(self, large_catalog: ToolCatalog) -> None:
        text = build_prompt_section(large_catalog, threshold_tokens=200, directory_budget_tokens=300)
        assert "tool_search" in text
        assert "expand_tools" in text
        assert "more tools" in text


class TestManagerWireIn:
    def test_empty_tools_returns_empty(self) -> None:
        assert build_tools_prompt_section([]) == ""

    def test_small_catalog_inline(self) -> None:
        tools = [("git", "git_diff", "show diff", {"path": "string"})]
        out = build_tools_prompt_section(tools, threshold_tokens=10_000)
        assert "MCP tools available" in out
        assert "tool_search" not in out

    def test_large_catalog_swaps_to_meta_tool(self) -> None:
        tools = [
            (
                f"s{i}",
                f"s{i}.tool_{i}",
                "do work over inputs and produce structured output payload",
                {"a": "string", "b": "object"},
            )
            for i in range(60)
        ]
        out = build_tools_prompt_section(tools, threshold_tokens=200, directory_budget_tokens=300)
        assert "tool_search" in out
        assert "expand_tools" in out

    def test_disabled_forces_inline_even_when_large(self) -> None:
        tools = [(f"s{i}", f"tool_{i}", "summary", {}) for i in range(60)]
        out = build_tools_prompt_section(tools, threshold_tokens=10, enabled=False)
        assert "tool_search" not in out
        assert "MCP tools available" in out


def _counter_value(counter: object, **labels: str) -> float:
    """Read a labelled Prometheus counter via the public _value attribute.

    Falls back to 0.0 when the prometheus_client stubs are in use.
    """
    try:
        labelled = counter.labels(**labels)  # type: ignore[attr-defined]
        return float(labelled._value.get())  # type: ignore[attr-defined]
    except Exception:
        return 0.0


class TestMetricsCounter:
    def test_search_invocation_increments_counter(self, small_catalog: ToolCatalog) -> None:
        from bernstein.core.protocols.mcp.mcp_tool_search import (
            mcp_tool_search_invocations_total,
        )

        before = _counter_value(mcp_tool_search_invocations_total, mode="search")
        search_tools(small_catalog, "git")
        after = _counter_value(mcp_tool_search_invocations_total, mode="search")
        assert after >= before + 1

    def test_expand_invocation_increments_counter(self, small_catalog: ToolCatalog) -> None:
        from bernstein.core.protocols.mcp.mcp_tool_search import (
            mcp_tool_search_invocations_total,
        )

        before = _counter_value(mcp_tool_search_invocations_total, mode="expand")
        expand_tools(small_catalog, ["git_diff"])
        after = _counter_value(mcp_tool_search_invocations_total, mode="expand")
        assert after >= before + 1


class TestSearchHitDataclass:
    def test_search_hit_fields(self, small_catalog: ToolCatalog) -> None:
        engine = ToolSearchEngine(small_catalog)
        hits = engine.search("commit")
        assert hits
        hit = hits[0]
        assert isinstance(hit, SearchHit)
        assert hit.name
        assert hit.server
        assert hit.summary
