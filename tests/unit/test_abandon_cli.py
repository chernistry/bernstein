"""Unit tests for ``bernstein abandonments`` CLI commands (#1350)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.abandonments_cmd import (
    abandonments_group,
    render_rows_jsonl,
    render_rows_table_text,
)
from bernstein.core.tasks.abandon import (
    AbandonmentLedger,
    AbandonReason,
    new_abandonment,
)


@pytest.fixture()
def seeded_workdir(tmp_path: Path) -> Path:
    ledger = AbandonmentLedger(tmp_path / ".sdd")
    ledger.append(
        new_abandonment(
            task_id="T-1",
            reason=AbandonReason.OUT_OF_SCOPE,
            detail="spec mismatch",
            role="backend",
            adapter="claude",
            attempts=0,
            timestamp=1_700_000_000.0,
        )
    )
    ledger.append(
        new_abandonment(
            task_id="T-2",
            reason=AbandonReason.BUDGET_EXCEEDED,
            detail="cap hit",
            role="qa",
            adapter="codex",
            attempts=1,
            timestamp=1_700_000_100.0,
        )
    )
    return tmp_path


class TestAbandonmentsListCmd:
    def test_empty_workdir_prints_friendly_notice(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["list", "--workdir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No abandonments recorded" in result.output

    def test_list_renders_rows(self, seeded_workdir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["list", "--workdir", str(seeded_workdir)])
        assert result.exit_code == 0
        # Newest first → T-2 should appear before T-1
        assert "T-2" in result.output
        assert "T-1" in result.output
        assert result.output.index("T-2") < result.output.index("T-1")

    def test_list_honours_limit(self, seeded_workdir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            abandonments_group,
            ["list", "--workdir", str(seeded_workdir), "--limit", "1"],
        )
        assert result.exit_code == 0
        assert "T-2" in result.output
        assert "T-1" not in result.output


class TestAbandonmentsStatsCmd:
    def test_empty_workdir_prints_friendly_notice(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["stats", "--workdir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No abandonments recorded" in result.output

    def test_stats_renders_totals(self, seeded_workdir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["stats", "--workdir", str(seeded_workdir)])
        assert result.exit_code == 0
        assert "Total abandonments" in result.output
        assert "out_of_scope" in result.output
        assert "budget_exceeded" in result.output
        assert "backend" in result.output
        assert "claude" in result.output


class TestSnapshotRenderers:
    def test_render_rows_jsonl_is_deterministic(self, seeded_workdir: Path) -> None:
        ledger_path = seeded_workdir / ".sdd" / "runtime" / "abandonments.jsonl"
        raw = render_rows_jsonl(ledger_path)
        lines = [line for line in raw.splitlines() if line.strip()]
        assert len(lines) == 2
        decoded = [json.loads(line) for line in lines]
        # First appended row is the older T-1
        assert decoded[0]["task_id"] == "T-1"
        assert decoded[1]["task_id"] == "T-2"

    def test_render_rows_jsonl_missing_path_returns_empty(self, tmp_path: Path) -> None:
        assert render_rows_jsonl(tmp_path / "nonexistent.jsonl") == ""

    def test_render_rows_jsonl_keys_are_sorted(self, seeded_workdir: Path) -> None:
        ledger_path = seeded_workdir / ".sdd" / "runtime" / "abandonments.jsonl"
        raw = render_rows_jsonl(ledger_path)
        for line in raw.splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            keys = list(data.keys())
            assert keys == sorted(keys)

    def test_render_rows_table_text_empty(self) -> None:
        assert render_rows_table_text([]) == "(empty)\n"

    def test_render_rows_table_text_has_header(self) -> None:
        rows = [
            {
                "task_id": "T-1",
                "role": "backend",
                "reason": "out_of_scope",
                "adapter": "claude",
                "attempts": 0,
                "detail": "spec mismatch",
                "timestamp": 1_700_000_000.0,
            }
        ]
        out = render_rows_table_text(rows)
        assert out.splitlines()[0] == "timestamp\ttask_id\trole\treason\tadapter\tattempts\tdetail"

    def test_render_rows_table_text_snapshot(self) -> None:
        rows = [
            {
                "task_id": "T-2",
                "role": "qa",
                "reason": "budget_exceeded",
                "adapter": "codex",
                "attempts": 1,
                "detail": "cap hit",
                "timestamp": 1_700_000_100.0,
            },
            {
                "task_id": "T-1",
                "role": "backend",
                "reason": "out_of_scope",
                "adapter": "claude",
                "attempts": 0,
                "detail": "spec mismatch",
                "timestamp": 1_700_000_000.0,
            },
        ]
        out = render_rows_table_text(rows)
        expected = (
            "timestamp\ttask_id\trole\treason\tadapter\tattempts\tdetail\n"
            "2023-11-14T22:15:00Z\tT-2\tqa\tbudget_exceeded\tcodex\t1\tcap hit\n"
            "2023-11-14T22:13:20Z\tT-1\tbackend\tout_of_scope\tclaude\t0\tspec mismatch\n"
        )
        assert out == expected
