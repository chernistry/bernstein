"""Tests for ``bernstein doctor --suggest-docs``.

Covers the loader, top-N truncation, the rendered hint line, and the
Click flag wiring. The loader must degrade gracefully on missing,
empty, malformed, and partially invalid input so the flag never
crashes the diagnostic surface.
"""

from __future__ import annotations

import ast
import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from bernstein.cli.doctor.suggest_docs import (
    DEFAULT_TOP_N,
    UnansweredTopic,
    format_topic_line,
    hint_line,
    load_unanswered_topics,
    render_suggestions,
    top_n_topics,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_topic(count: int, topic: str = "t") -> UnansweredTopic:
    return UnansweredTopic(
        topic=f"{topic}-{count}",
        related_command="bernstein doctor",
        doc_page_proposed=f"docs/{topic}-{count}.md",
        source="operator-curated-2026-05-19",
        count=count,
    )


# ---------------------------------------------------------------------------
# load_unanswered_topics
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty_list(tmp_path: Path) -> None:
    """A non-existent path is the expected fresh-install state."""
    missing = tmp_path / "does-not-exist.json"
    assert load_unanswered_topics(missing) == []


def test_load_empty_array_returns_empty_list(tmp_path: Path) -> None:
    """An explicit empty array is a valid no-gaps signal."""
    path = tmp_path / "empty.json"
    _write_json(path, [])
    assert load_unanswered_topics(path) == []


def test_load_empty_file_returns_empty_list(tmp_path: Path) -> None:
    """A zero-byte file is malformed JSON and must not raise."""
    path = tmp_path / "blank.json"
    path.write_text("", encoding="utf-8")
    assert load_unanswered_topics(path) == []


def test_load_malformed_json_returns_empty_list(tmp_path: Path) -> None:
    """A syntactically invalid file must not crash the doctor."""
    path = tmp_path / "broken.json"
    path.write_text("{ not valid json ::", encoding="utf-8")
    assert load_unanswered_topics(path) == []


def test_load_non_list_root_returns_empty_list(tmp_path: Path) -> None:
    """The schema requires a top-level array; an object is rejected."""
    path = tmp_path / "object.json"
    _write_json(path, {"topic": "x"})
    assert load_unanswered_topics(path) == []


def test_load_skips_entries_missing_required_keys(tmp_path: Path) -> None:
    """Partial entries are dropped; valid siblings still load."""
    path = tmp_path / "mixed.json"
    _write_json(
        path,
        [
            {"topic": "no count", "related_command": "c", "doc_page_proposed": "p", "source": "s"},
            {
                "topic": "good",
                "related_command": "bernstein doctor",
                "doc_page_proposed": "docs/good.md",
                "source": "operator-curated-2026-05-19",
                "count": 3,
            },
        ],
    )
    loaded = load_unanswered_topics(path)
    assert [t.topic for t in loaded] == ["good"]


def test_load_skips_entries_with_blank_fields(tmp_path: Path) -> None:
    """Whitespace-only required fields are treated as missing."""
    path = tmp_path / "blanks.json"
    _write_json(
        path,
        [
            {
                "topic": "   ",
                "related_command": "c",
                "doc_page_proposed": "p",
                "source": "s",
                "count": 1,
            },
        ],
    )
    assert load_unanswered_topics(path) == []


def test_load_rejects_non_integer_count(tmp_path: Path) -> None:
    """The ``count`` field must be a true integer, not a string or bool."""
    path = tmp_path / "bad_counts.json"
    _write_json(
        path,
        [
            {
                "topic": "string-count",
                "related_command": "c",
                "doc_page_proposed": "p",
                "source": "s",
                "count": "5",
            },
            {
                "topic": "bool-count",
                "related_command": "c",
                "doc_page_proposed": "p",
                "source": "s",
                "count": True,
            },
        ],
    )
    assert load_unanswered_topics(path) == []


def test_load_packaged_seed_file_parses_cleanly() -> None:
    """The shipped seed file must round-trip through the loader.

    Pins the contract that the curated file the maintainer refreshes
    on each release never ships in a state the loader rejects.
    """
    topics = load_unanswered_topics()
    assert topics, "packaged seed file should contain at least one entry"
    for topic in topics:
        assert topic.topic
        assert topic.related_command
        assert topic.doc_page_proposed
        assert topic.source
        assert isinstance(topic.count, int)


# ---------------------------------------------------------------------------
# top_n_topics
# ---------------------------------------------------------------------------


def test_top_n_returns_highest_counts_first() -> None:
    """Sort by ``count`` descending so the busiest gaps surface first."""
    topics = [_make_topic(1), _make_topic(7), _make_topic(3)]
    out = top_n_topics(topics, n=2)
    assert [t.count for t in out] == [7, 3]


def test_top_n_truncates_to_limit() -> None:
    """The renderer must not flood the doctor footer."""
    topics = [_make_topic(i) for i in range(1, 11)]
    out = top_n_topics(topics, n=DEFAULT_TOP_N)
    assert len(out) == DEFAULT_TOP_N
    assert [t.count for t in out] == [10, 9, 8, 7, 6]


def test_top_n_clamps_when_input_shorter_than_n() -> None:
    """Asking for more entries than exist returns the whole list."""
    topics = [_make_topic(1), _make_topic(2)]
    out = top_n_topics(topics, n=10)
    assert len(out) == 2


def test_top_n_with_zero_returns_empty() -> None:
    """``n=0`` is treated as "no suggestions requested"."""
    topics = [_make_topic(1), _make_topic(2)]
    assert top_n_topics(topics, n=0) == []


def test_top_n_with_negative_n_returns_empty() -> None:
    """Defensive: negative limits must not Python-slice into a tail."""
    assert top_n_topics([_make_topic(1)], n=-3) == []


def test_top_n_topics_sorts_iterable_without_intermediate_list_copy() -> None:
    """The sorter can consume the iterable directly."""
    module = ast.parse(Path("src/bernstein/cli/doctor/suggest_docs.py").read_text(encoding="utf-8"))
    top_n = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "top_n_topics")
    list_copy_assignments = [
        node
        for node in ast.walk(top_n)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "list"
    ]

    assert list_copy_assignments == []


# ---------------------------------------------------------------------------
# format_topic_line + render_suggestions + hint_line
# ---------------------------------------------------------------------------


def test_format_topic_line_uses_expected_schema() -> None:
    """The arrow-prefixed format is the operator-facing contract."""
    topic = UnansweredTopic(
        topic="How to wire a custom adapter",
        related_command="bernstein adapters add",
        doc_page_proposed="docs/adapters/custom-quickstart.md",
        source="operator-curated-2026-05-19",
        count=4,
    )
    line = format_topic_line(topic)
    assert line == (
        "-> How to wire a custom adapter (related: bernstein adapters add). "
        "proposed page: docs/adapters/custom-quickstart.md"
    )


def test_render_suggestions_prints_each_topic_as_hint_line() -> None:
    """Top-N truncation must propagate from the public renderer."""
    buf = StringIO()
    console = Console(file=buf, width=120, color_system=None)
    topics = [_make_topic(i) for i in range(1, 8)]
    render_suggestions(console, topics, limit=3)
    output = buf.getvalue()
    assert "Top documentation gaps" in output
    # Highest three counts (7, 6, 5) appear; lower ones do not.
    assert "t-7" in output
    assert "t-6" in output
    assert "t-5" in output
    assert "t-1" not in output
    # Every emitted entry uses the arrow-prefixed hint format.
    arrow_lines = [line for line in output.splitlines() if line.startswith("-> ")]
    assert len(arrow_lines) == 3


def test_render_suggestions_handles_empty_list_gracefully() -> None:
    """An empty curated list must still produce a friendly note."""
    buf = StringIO()
    console = Console(file=buf, width=120, color_system=None)
    render_suggestions(console, [], limit=DEFAULT_TOP_N)
    output = buf.getvalue()
    assert "No documentation gaps recorded" in output


def test_hint_line_matches_acceptance_criterion() -> None:
    """The trailing hint must match the exact line the ticket calls out."""
    assert hint_line() == ("Run `bernstein doctor --suggest-docs` to see the top documentation gaps.")


# ---------------------------------------------------------------------------
# Click flag wiring
# ---------------------------------------------------------------------------


def test_doctor_suggest_docs_flag_prints_curated_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The flag short-circuits the doctor and surfaces the curated list."""
    from click.testing import CliRunner

    from bernstein.cli.commands.advanced_cmd import doctor as doctor_group

    fixture = tmp_path / "_unanswered.json"
    _write_json(
        fixture,
        [
            {
                "topic": "alpha gap",
                "related_command": "bernstein doctor",
                "doc_page_proposed": "docs/alpha.md",
                "source": "operator-curated-2026-05-19",
                "count": 9,
            },
            {
                "topic": "beta gap",
                "related_command": "bernstein run",
                "doc_page_proposed": "docs/beta.md",
                "source": "operator-curated-2026-05-19",
                "count": 4,
            },
        ],
    )
    monkeypatch.setattr(
        "bernstein.cli.doctor.suggest_docs._packaged_path",
        lambda: fixture,
    )

    runner = CliRunner()
    result = runner.invoke(doctor_group, ["--suggest-docs"])
    assert result.exit_code == 0, result.output
    assert "Top documentation gaps" in result.output
    assert "alpha gap" in result.output
    assert "beta gap" in result.output
    # The trailing hint is for default runs only; --suggest-docs must
    # not double-emit it.
    assert "Run `bernstein doctor --suggest-docs`" not in result.output
