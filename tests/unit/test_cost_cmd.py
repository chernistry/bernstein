"""Tests for the `bernstein cost` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.cost import cost_cmd


@pytest.fixture()
def metrics_dir(tmp_path: Path) -> Path:
    mdir = tmp_path / "metrics"
    mdir.mkdir()

    # tasks.jsonl — two tasks with different models
    tasks = [
        {
            "task_id": "abc123",
            "role": "backend",
            "model": "claude-sonnet-4-6",
            "provider": "anthropic",
            "duration_seconds": 42.5,
            "tokens_prompt": 1000,
            "tokens_completion": 500,
            "cost_usd": 0.0025,
        },
        {
            "task_id": "def456",
            "role": "qa",
            "model": "claude-haiku-4-5",
            "provider": "anthropic",
            "duration_seconds": 20.0,
            "tokens_prompt": 400,
            "tokens_completion": 200,
            "cost_usd": 0.0005,
        },
        # duplicate of abc123 — should be deduplicated (last wins)
        {
            "task_id": "abc123",
            "role": "backend",
            "model": "claude-sonnet-4-6",
            "provider": "anthropic",
            "duration_seconds": 45.0,
            "tokens_prompt": 1100,
            "tokens_completion": 550,
            "cost_usd": 0.0027,
        },
    ]
    (mdir / "tasks.jsonl").write_text("\n".join(json.dumps(r) for r in tasks))

    # api_usage_2026-01-01.jsonl — minimal records
    api_records = [
        {
            "timestamp": 1000.0,
            "metric_type": "api_usage",
            "value": 0,
            "labels": {"provider": "anthropic", "model": "claude-sonnet-4-6", "success": "True"},
        }
    ]
    (mdir / "api_usage_2026-01-01.jsonl").write_text("\n".join(json.dumps(r) for r in api_records))

    return mdir


def test_cost_table_contains_expected_columns(metrics_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cost_cmd, ["--metrics-dir", str(metrics_dir)])
    assert result.exit_code == 0, result.output
    output = result.output
    # Column headers
    assert "Model" in output
    assert "Tasks" in output
    assert "Tokens In" in output
    assert "Tokens Out" in output
    assert "Cost USD" in output
    assert "Avg Dur" in output  # header may be truncated by terminal width
    # Data rows
    assert "claude-sonnet-4-6" in output
    assert "claude-haiku-4-5" in output
    # Totals row
    assert "TOTAL" in output


def test_cost_json_output(metrics_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cost_cmd, ["--metrics-dir", str(metrics_dir), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "rows" in data
    assert "totals" in data
    models = {r["model"] for r in data["rows"]}
    assert "claude-sonnet-4-6" in models
    assert "claude-haiku-4-5" in models
    # Deduplication: abc123 appears once
    sonnet_row = next(r for r in data["rows"] if r["model"] == "claude-sonnet-4-6")
    assert sonnet_row["tasks"] == 1


def test_cost_missing_metrics_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cost_cmd, ["--metrics-dir", str(tmp_path / "nonexistent")])
    assert result.exit_code != 0


def test_cost_missing_metrics_dir_json(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cost_cmd, ["--metrics-dir", str(tmp_path / "nonexistent"), "--json"])
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert "error" in data


def test_cost_empty_metrics_dir(tmp_path: Path) -> None:
    mdir = tmp_path / "metrics"
    mdir.mkdir()
    runner = CliRunner()
    result = runner.invoke(cost_cmd, ["--metrics-dir", str(mdir)])
    assert result.exit_code == 0
    assert "No metrics data found" in result.output
