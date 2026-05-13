"""Tests for the EU AI Act Article 12 evidence renderer.

Validates the CSV / PDF / paragraph-map outputs of
:mod:`bernstein.core.compliance.article12`.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import Any

import pypdf
import pytest

from bernstein.core.compliance.article12 import (
    ARTICLE12_PARAGRAPH_MAP,
    render_csv,
    render_pdf,
)
from bernstein.core.lineage.entry import LineageEntry


def _entry(
    *,
    path: str = "src/example.py",
    content: str = "sha256:" + "a" * 64,
    agent_id: str = "agent:claude-worker-1",
    ts_ns: int = 1_715_000_000_000_000_000,
    tool_call_id: str = "tc-1",
    span_id: str = "00f067aa0ba902b7",
    kid: str = "kid-1",
    parents: list[str] | None = None,
    kind: str = "file",
) -> LineageEntry:
    return LineageEntry(
        v=1,
        artefact_path=path,
        artefact_kind=kind,
        content_hash=content,
        parent_hashes=parents or [],
        agent_id=agent_id,
        agent_card_kid=kid,
        tool_call_id=tool_call_id,
        span_id=span_id,
        ts_ns=ts_ns,
        operator_hmac="deadbeef",
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


class TestRenderCSV:
    def test_round_trip_through_dict_reader(self) -> None:
        entries = [
            _entry(path="a.py", ts_ns=1),
            _entry(path="b.py", ts_ns=2, agent_id="agent:claude-worker-2"),
        ]
        csv_text = render_csv(entries)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert len(rows) == 2
        # Stable header
        assert "artefact_path" in rows[0]
        assert "agent_id" in rows[0]
        assert "ts_ns" in rows[0]
        assert "content_hash" in rows[0]
        assert rows[0]["artefact_path"] == "a.py"
        assert rows[1]["agent_id"] == "agent:claude-worker-2"

    def test_handles_empty(self) -> None:
        csv_text = render_csv([])
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert rows == []
        # Header row still present
        assert "artefact_path" in csv_text

    def test_escapes_path_with_comma_and_quote(self) -> None:
        entries = [_entry(path='weird,name"with-quote.py')]
        csv_text = render_csv(entries)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert rows[0]["artefact_path"] == 'weird,name"with-quote.py'


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


class TestRenderPDF:
    def test_pdf_parses_and_contains_org_and_period(self) -> None:
        entries = [_entry(ts_ns=1_700_000_000_000_000_000)]
        pdf_bytes = render_pdf(
            entries,
            org="Acme Corp",
            period=("2026-01-01", "2026-05-13"),
        )
        assert pdf_bytes[:4] == b"%PDF"
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(p.extract_text() for p in reader.pages)
        assert "Acme Corp" in text
        assert "2026-01-01" in text
        assert "2026-05-13" in text

    def test_pdf_renders_for_empty_period(self) -> None:
        pdf_bytes = render_pdf([], org="Empty Org", period=("2026-01-01", "2026-01-02"))
        assert pdf_bytes[:4] == b"%PDF"
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join(p.extract_text() for p in reader.pages)
        assert "Empty Org" in text


# ---------------------------------------------------------------------------
# Paragraph map
# ---------------------------------------------------------------------------


class TestArticle12ParagraphMap:
    def test_required_paragraph_keys_present(self) -> None:
        expected = {"12(1)", "12(2)(a)", "12(2)(b)", "12(2)(c)", "12(2)(d)", "12(3)"}
        assert expected <= set(ARTICLE12_PARAGRAPH_MAP)

    def test_every_paragraph_produces_well_typed_output(self) -> None:
        entries = [
            _entry(path="a.py", agent_id="agent:a", ts_ns=1_700_000_000_000_000_000),
            _entry(path="b.py", agent_id="agent:b", ts_ns=1_710_000_000_000_000_000),
            _entry(path="a.py", agent_id="agent:b", ts_ns=1_720_000_000_000_000_000),
        ]
        period = ("2026-01-01", "2026-05-13")
        for paragraph, fn in ARTICLE12_PARAGRAPH_MAP.items():
            assert isinstance(paragraph, str)
            fact: dict[str, Any] = fn(entries, period)
            assert isinstance(fact, dict)
            # Every fact must self-describe its paragraph + a value field.
            assert fact["paragraph"] == paragraph
            assert "value" in fact
            assert "description" in fact

    def test_paragraph_12_1_counts_entries(self) -> None:
        fn: Callable[..., dict[str, Any]] = ARTICLE12_PARAGRAPH_MAP["12(1)"]
        entries = [_entry(ts_ns=i) for i in range(1, 6)]
        result = fn(entries, ("2026-01-01", "2026-05-13"))
        assert result["value"] == 5

    def test_paragraph_12_2_a_period_of_use(self) -> None:
        fn = ARTICLE12_PARAGRAPH_MAP["12(2)(a)"]
        entries = [
            _entry(ts_ns=10),
            _entry(ts_ns=30),
            _entry(ts_ns=20),
        ]
        result = fn(entries, ("2026-01-01", "2026-05-13"))
        assert result["value"]["first_ts_ns"] == 10
        assert result["value"]["last_ts_ns"] == 30

    def test_paragraph_12_2_c_unique_artefact_paths(self) -> None:
        fn = ARTICLE12_PARAGRAPH_MAP["12(2)(c)"]
        entries = [
            _entry(path="a.py"),
            _entry(path="b.py"),
            _entry(path="a.py"),
        ]
        result = fn(entries, ("2026-01-01", "2026-05-13"))
        assert result["value"] == sorted(["a.py", "b.py"])

    def test_paragraph_12_2_d_unique_agent_ids(self) -> None:
        fn = ARTICLE12_PARAGRAPH_MAP["12(2)(d)"]
        entries = [
            _entry(agent_id="agent:a"),
            _entry(agent_id="agent:b"),
            _entry(agent_id="agent:a"),
        ]
        result = fn(entries, ("2026-01-01", "2026-05-13"))
        assert result["value"] == sorted(["agent:a", "agent:b"])

    def test_paragraph_12_3_retention_check(self) -> None:
        fn = ARTICLE12_PARAGRAPH_MAP["12(3)"]
        # 6+ months → ok
        result = fn([], ("2026-01-01", "2026-07-15"))
        assert result["value"]["meets_minimum"] is True
        # under 6 months → not ok
        result = fn([], ("2026-01-01", "2026-02-15"))
        assert result["value"]["meets_minimum"] is False

    def test_empty_period_paragraphs_report_zero(self) -> None:
        for paragraph, fn in ARTICLE12_PARAGRAPH_MAP.items():
            result = fn([], ("2026-01-01", "2026-05-13"))
            assert result["paragraph"] == paragraph
            value = result["value"]
            # 12(2)(b) ("reference DB") is inherently n/a from lineage
            # alone; everything else must hit one of the zero shapes
            # (None | 0 | [] | dict).
            if paragraph == "12(2)(b)":
                assert value is None
                continue
            if isinstance(value, list):
                assert value == []
            elif isinstance(value, int):
                assert value == 0
            elif isinstance(value, dict):
                assert value is not None
            else:
                pytest.fail(f"unexpected type for {paragraph}: {type(value)}")
