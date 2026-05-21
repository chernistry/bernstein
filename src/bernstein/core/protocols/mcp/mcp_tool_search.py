"""MCP tool-search lazy loading.

Keeps full MCP tool descriptions out of an agent's system prompt by
exposing a single ``tool_search`` meta-tool plus a compact directory of
tool names + one-line summaries.  Full JSON Schemas are only fetched on
demand via :func:`expand_tools` once the agent has chosen what it needs.

The pattern is the lazy-loading half of "tool search" from
awesome-agentic-patterns: with 7+ MCP servers the full catalog can run
to ~67k tokens - ruinous for short-lived agents whose whole reason for
existing is fast spawn time.  When the catalog exceeds a configurable
threshold we serve a directory + search affordance instead.

Usage::

    from bernstein.core.protocols.mcp.mcp_tool_search import (
        ToolCatalog, ToolEntry, ToolSearchEngine, build_prompt_section,
    )

    catalog = ToolCatalog([ToolEntry(name="git_diff", ...), ...])
    engine = ToolSearchEngine(catalog)
    hits = engine.search("git diff", limit=5)
    schemas = catalog.expand([h.name for h in hits])
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from bernstein.core.observability.prometheus import Counter as PromCounter
from bernstein.core.observability.prometheus import registry

logger = logging.getLogger(__name__)


_AVG_CHARS_PER_TOKEN: float = 4.0
_MAX_SUMMARY_CHARS: int = 120
_BM25_K1: float = 1.5
_BM25_B: float = 0.75
_TOKEN_RE: re.Pattern[str] = re.compile(r"[A-Za-z0-9]+")


mcp_tool_search_invocations_total: PromCounter = PromCounter(
    "mcp_tool_search_invocations_total",
    "Number of times the MCP tool_search meta-tool was invoked.",
    labelnames=["mode"],
    registry=registry,
)


@dataclass(frozen=True)
class ToolEntry:
    """A single MCP tool record kept by :class:`ToolCatalog`.

    Attributes:
        name: Fully-qualified tool name (e.g. ``"github.create_issue"``).
        summary: One-line description used in the directory and for ranking.
        server: Originating MCP server name.
        schema: Full JSON Schema for the tool's input parameters.
    """

    name: str
    summary: str
    server: str
    schema: Mapping[str, Any] = field(default_factory=dict)

    @property
    def directory_line(self) -> str:
        """Return a compact one-line catalog entry for prompt injection."""
        truncated = (
            self.summary if len(self.summary) <= _MAX_SUMMARY_CHARS else self.summary[: _MAX_SUMMARY_CHARS - 1] + "…"
        )
        return f"- {self.name} ({self.server}): {truncated}"


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def estimate_tokens(text: str) -> int:
    """Cheap token-count estimate using a chars-per-token heuristic.

    Avoids pulling in tiktoken - close enough for budget gating.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _AVG_CHARS_PER_TOKEN))


@dataclass(frozen=True)
class SearchHit:
    """One ranked search result."""

    name: str
    server: str
    summary: str
    score: float


class ToolCatalog:
    """Indexed collection of :class:`ToolEntry` records.

    Provides directory rendering, schema expansion, and the underlying
    statistics consumed by :class:`ToolSearchEngine`.
    """

    def __init__(self, entries: Iterable[ToolEntry]) -> None:
        self._entries: dict[str, ToolEntry] = {}
        for entry in entries:
            self._entries[entry.name] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._entries

    @property
    def entries(self) -> list[ToolEntry]:
        return list(self._entries.values())

    @property
    def names(self) -> list[str]:
        return list(self._entries)

    def get(self, name: str) -> ToolEntry | None:
        return self._entries.get(name)

    def expand(self, names: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Return full JSON schemas for *names*.

        Unknown names are silently skipped; callers can detect a miss by
        comparing keys.
        """
        out: dict[str, dict[str, Any]] = {}
        for name in names:
            entry = self._entries.get(name)
            if entry is None:
                continue
            out[entry.name] = {
                "name": entry.name,
                "server": entry.server,
                "description": entry.summary,
                "input_schema": dict(entry.schema),
            }
        return out

    def directory_lines(self) -> list[str]:
        return [entry.directory_line for entry in self._entries.values()]

    def estimate_full_tokens(self) -> int:
        """Estimate token cost of the full catalog with schemas inlined."""
        total = 0
        for entry in self._entries.values():
            total += estimate_tokens(entry.name)
            total += estimate_tokens(entry.summary)
            total += estimate_tokens(_schema_blob(entry.schema))
        return total

    def estimate_directory_tokens(self) -> int:
        """Estimate token cost of just the names + summaries directory."""
        return sum(estimate_tokens(line) for line in self.directory_lines())


def _schema_blob(schema: Mapping[str, Any]) -> str:
    """Stringify a JSON Schema for token estimation purposes only."""
    if not schema:
        return ""
    parts: list[str] = []
    for key, value in schema.items():
        parts.append(key)
        if isinstance(value, Mapping):
            parts.append(_schema_blob(value))
        elif isinstance(value, list | tuple):
            parts.extend(str(v) for v in value)
        else:
            parts.append(str(value))
    return " ".join(parts)


class ToolSearchEngine:
    """BM25 ranker over tool name + summary text.

    BM25 is plenty for v1 - names and summaries are short, the corpus is
    small (low hundreds of tools at most), and we want zero extra
    dependencies.  Vector embeddings are deferred per the ticket.
    """

    def __init__(self, catalog: ToolCatalog) -> None:
        self._catalog = catalog
        self._docs: dict[str, list[str]] = {}
        self._df: Counter[str] = Counter()
        self._avg_dl: float = 0.0
        self._build_index()

    def _build_index(self) -> None:
        total_dl = 0
        for entry in self._catalog.entries:
            tokens = _tokenize(f"{entry.name} {entry.summary} {entry.server}")
            self._docs[entry.name] = tokens
            for term in set(tokens):
                self._df[term] += 1
            total_dl += len(tokens)
        if self._docs:
            self._avg_dl = total_dl / len(self._docs)

    def _idf(self, term: str) -> float:
        n = len(self._docs)
        if n == 0:
            return 0.0
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _bm25(self, query_terms: list[str], doc_terms: list[str]) -> float:
        if not doc_terms:
            return 0.0
        dl = len(doc_terms)
        tf = Counter(doc_terms)
        score = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = self._idf(term)
            denom = f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / (self._avg_dl or 1.0))
            score += idf * (f * (_BM25_K1 + 1)) / denom
        return score

    def search(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        """Return the top-*limit* tools ranked for *query*.

        Falls back to a substring-match scan when BM25 produces no hits
        (covers the case where the agent searches for an exact tool name
        with no overlapping terms in summaries).
        """
        query_terms = _tokenize(query)
        hits: list[SearchHit] = []
        if query_terms:
            for name, doc_terms in self._docs.items():
                score = self._bm25(query_terms, doc_terms)
                if score <= 0:
                    continue
                entry = self._catalog.get(name)
                if entry is None:
                    continue
                hits.append(SearchHit(name=name, server=entry.server, summary=entry.summary, score=score))

        if not hits:
            needle = query.lower().strip()
            if needle:
                for entry in self._catalog.entries:
                    if needle in entry.name.lower() or needle in entry.summary.lower():
                        hits.append(
                            SearchHit(
                                name=entry.name,
                                server=entry.server,
                                summary=entry.summary,
                                score=0.0,
                            )
                        )

        hits.sort(key=lambda h: (-h.score, h.name))
        return hits[:limit]


def compact_descriptions(catalog: ToolCatalog, budget_tokens: int) -> list[str]:
    """Pack as many directory lines as fit inside *budget_tokens*.

    Returns the lines in catalog order; truncation is by line, not by
    character, so partial entries never leak into the prompt.
    """
    if budget_tokens <= 0:
        return []
    selected: list[str] = []
    spent = 0
    for line in catalog.directory_lines():
        cost = estimate_tokens(line)
        if spent + cost > budget_tokens:
            break
        selected.append(line)
        spent += cost
    return selected


def expand_tools(catalog: ToolCatalog, names: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Module-level shim around :meth:`ToolCatalog.expand`.

    Records the invocation against the Prometheus counter so the lazy
    path is observable.
    """
    name_list = list(names)
    try:
        mcp_tool_search_invocations_total.labels(mode="expand").inc()
    except Exception:
        logger.debug("Failed to record mcp_tool_search expand invocation", exc_info=True)
    return catalog.expand(name_list)


def search_tools(catalog: ToolCatalog, query: str, *, limit: int = 10) -> list[SearchHit]:
    """Convenience search that also bumps the Prometheus invocation counter."""
    try:
        mcp_tool_search_invocations_total.labels(mode="search").inc()
    except Exception:
        logger.debug("Failed to record mcp_tool_search search invocation", exc_info=True)
    return ToolSearchEngine(catalog).search(query, limit=limit)


def should_use_tool_search(catalog: ToolCatalog, threshold_tokens: int) -> bool:
    """Return True when the full catalog exceeds *threshold_tokens*."""
    if threshold_tokens <= 0:
        return False
    return catalog.estimate_full_tokens() > threshold_tokens


_META_TOOL_DESCRIPTION = (
    "tool_search(query, limit=10): search the MCP tool directory by keyword. "
    "Returns ranked tool names + summaries. "
    "Call expand_tools(names=[...]) to fetch full JSON schemas before invoking."
)


def build_prompt_section(
    catalog: ToolCatalog,
    *,
    threshold_tokens: int,
    directory_budget_tokens: int = 1500,
) -> str:
    """Construct the MCP-tools prompt section.

    When the full catalog fits inside *threshold_tokens* we return the
    inline tool list (one line per tool, schemas not included).  When it
    overflows we return the lazy section: a meta-tool description plus a
    truncated directory bounded by *directory_budget_tokens*.
    """
    if not should_use_tool_search(catalog, threshold_tokens):
        lines = ["MCP tools available:", *catalog.directory_lines()]
        return "\n".join(lines)

    directory = compact_descriptions(catalog, directory_budget_tokens)
    sections = [
        "MCP tool catalog is large; use the meta-tool below to load schemas on demand.",
        _META_TOOL_DESCRIPTION,
        "",
        "Directory (names + 1-line summaries):",
        *directory,
    ]
    if len(directory) < len(catalog):
        sections.append(f"... ({len(catalog) - len(directory)} more tools, search to discover)")
    return "\n".join(sections)


__all__ = [
    "SearchHit",
    "ToolCatalog",
    "ToolEntry",
    "ToolSearchEngine",
    "build_prompt_section",
    "compact_descriptions",
    "estimate_tokens",
    "expand_tools",
    "mcp_tool_search_invocations_total",
    "search_tools",
    "should_use_tool_search",
]
