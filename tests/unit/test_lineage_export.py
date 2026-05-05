"""Unit tests for ``bernstein lineage export`` (regulator artefact)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.commands.lineage_export_cmd import (
    lineage_export_cmd,
    render_csv,
    render_html,
    render_jsonld,
)
from bernstein.core.persistence.lineage import (
    AgentRef,
    ArtifactRef,
    LineageRecord,
    LineageWriter,
)


def _emit_records(sdd: Path, run_id: str = "run-1", count: int = 3) -> None:
    writer = LineageWriter.for_run(run_id, sdd)
    for i in range(count):
        writer.emit(
            LineageRecord(
                output_artifact=ArtifactRef(
                    path=f"src/f{i}.py",
                    sha256=("a" * 64)[:60] + f"{i:04d}",
                    line_start=1,
                    line_end=10,
                ),
                inputs=[ArtifactRef(path=f"src/in{i}.py", sha256="b" * 64)],
                producer=AgentRef(agent_id=f"agent-{i}", run_id=run_id, tick_id=f"t-{i}"),
                prompt_sha="c" * 64,
                model="claude-sonnet",
                cost_usd=0.01 * (i + 1),
                tokens=100 * (i + 1),
                timestamp=1700000000.0 + i,
                regulatory_class="production_detection_rule",
                customer_signature=f"sig-{i}",
            )
        )


def _row_for(rec: LineageRecord) -> dict[str, object]:
    from bernstein.cli.commands.lineage_export_cmd import _record_row

    return _record_row(rec)


class TestRenderCsv:
    def test_csv_has_header_and_rows(self) -> None:
        rec = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a" * 64),
            producer=AgentRef(agent_id="a", run_id="r"),
            regulatory_class="policy_edit",
        )
        out = render_csv([_row_for(rec)])
        rows = list(csv.DictReader(io.StringIO(out)))
        assert len(rows) == 1
        assert rows[0]["output_path"] == "x.py"
        assert rows[0]["regulatory_class"] == "policy_edit"

    def test_csv_handles_none_signature(self) -> None:
        rec = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a"),
            producer=AgentRef(agent_id="a", run_id="r"),
        )
        out = render_csv([_row_for(rec)])
        rows = list(csv.DictReader(io.StringIO(out)))
        assert rows[0]["customer_signature"] == ""


class TestRenderJsonld:
    def test_jsonld_is_schema_org_action_list(self) -> None:
        rec = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a" * 64, line_start=1, line_end=5),
            inputs=[ArtifactRef(path="in.py", sha256="b" * 64)],
            producer=AgentRef(agent_id="a", run_id="r-1"),
            regulatory_class="policy_edit",
        )
        text = render_jsonld([_row_for(rec)], run_id="r-1")
        doc = json.loads(text)
        assert doc["@context"] == "https://schema.org"
        assert doc["@type"] == "ItemList"
        assert doc["numberOfItems"] == 1
        action = doc["itemListElement"][0]
        assert action["@type"] == "Action"
        assert action["object"]["identifier"] == "x.py"
        assert action["object"]["sha256"] == "a" * 64
        assert action["instrument"][0]["identifier"] == "in.py"
        # additionalProperty preserves the regulatory_class
        props = {p["name"]: p["value"] for p in action["additionalProperty"]}
        assert props["regulatory_class"] == "policy_edit"


class TestRenderHtml:
    def test_html_is_self_contained_no_js_no_external(self) -> None:
        rec = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a" * 64, line_start=1, line_end=5),
            inputs=[ArtifactRef(path="in.py", sha256="b" * 64)],
            producer=AgentRef(agent_id="a", run_id="r-1"),
            regulatory_class="policy_edit",
            customer_signature="ZmFrZXNpZ25hdHVyZQ==",
        )
        text = render_html([_row_for(rec)], run_id="r-1")
        assert text.startswith("<!doctype html>")
        # No external JS or CSS, no images
        assert "<script" not in text
        assert "src=" not in text
        assert "href=" not in text
        # Style is inlined
        assert "<style>" in text
        assert "policy_edit" in text
        assert "ZmFrZXNpZ25hdHVyZQ==" in text

    def test_html_escapes_user_strings(self) -> None:
        rec = LineageRecord(
            output_artifact=ArtifactRef(path="<x>.py", sha256="a"),
            producer=AgentRef(agent_id="a&b", run_id="r"),
        )
        text = render_html([_row_for(rec)], run_id="<bad>")
        assert "<x>.py" not in text
        assert "&lt;x&gt;.py" in text
        assert "a&amp;b" in text
        assert "&lt;bad&gt;" in text


class TestExportCli:
    def test_csv_export_writes_file(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        _emit_records(sdd)
        out = tmp_path / "audit.csv"
        runner = CliRunner()
        result = runner.invoke(
            lineage_export_cmd,
            ["run-1", "--format", "csv", "--output", str(out), "--workdir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        text = out.read_text()
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 3
        assert rows[0]["regulatory_class"] == "production_detection_rule"

    def test_jsonld_export_writes_file(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        _emit_records(sdd, count=2)
        out = tmp_path / "audit.jsonld"
        runner = CliRunner()
        result = runner.invoke(
            lineage_export_cmd,
            ["run-1", "--format", "jsonld", "--output", str(out), "--workdir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        doc = json.loads(out.read_text())
        assert doc["numberOfItems"] == 2

    def test_html_export_writes_file(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        _emit_records(sdd, count=1)
        out = tmp_path / "audit.html"
        runner = CliRunner()
        result = runner.invoke(
            lineage_export_cmd,
            ["run-1", "--format", "html", "--output", str(out), "--workdir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        text = out.read_text()
        assert text.startswith("<!doctype html>")
        assert "<script" not in text

    def test_unknown_run_returns_nonzero(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        out = tmp_path / "audit.csv"
        runner = CliRunner()
        result = runner.invoke(
            lineage_export_cmd,
            ["nope", "--format", "csv", "--output", str(out), "--workdir", str(tmp_path)],
        )
        assert result.exit_code != 0
