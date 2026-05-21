"""Integration tests for the calibration pipeline.

Exercises the end-to-end flow: ``log_decision`` -> ``load_log`` -> CLI
``bernstein eval calibration report``. Each test wires the production CLI
through Click's test runner against a temporary log file to verify the
operator-visible surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.commands.eval_benchmark_cmd import eval_group
from bernstein.eval.calibration import (
    compute_report,
    load_log,
    log_decision,
)


def _log_batch(log_path: Path, *, n: int = 20) -> None:
    """Seed the log with a deterministic batch of records."""
    for i in range(n):
        prob = (i + 0.5) / n
        won = i >= (n // 2)
        log_decision(
            decision_kind="model_route" if i % 2 == 0 else "judge",
            policy_path="bandit/v1",
            predicted_prob=prob,
            observed_outcome=won,
            log_path=log_path,
            timestamp=float(i),
        )


def test_pipeline_log_to_report_round_trip(tmp_path: Path) -> None:
    """Logged decisions feed into ``compute_report`` without loss."""
    log = tmp_path / "calibration.jsonl"
    _log_batch(log, n=20)
    records = load_log(log)
    assert len(records) == 20
    report = compute_report(records)
    assert report.decisions == 20
    assert report.brier is not None
    assert report.ece is not None


def test_cli_report_empty_log(tmp_path: Path) -> None:
    """Running the CLI against an empty log returns null Brier/ECE - no crash."""
    log = tmp_path / "calibration.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["calibration", "report", "--log-path", str(log), "--since", "7d"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decisions"] == 0
    assert payload["brier"] is None
    assert payload["ece"] is None
    assert payload["since"] == "7d"


def test_cli_report_with_records(tmp_path: Path) -> None:
    """The CLI reports correct decision counts and finite Brier/ECE."""
    log = tmp_path / "calibration.jsonl"
    _log_batch(log, n=20)
    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["calibration", "report", "--log-path", str(log)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["decisions"] == 20
    assert 0.0 <= payload["brier"] <= 1.0
    assert 0.0 <= payload["ece"] <= 1.0
    assert len(payload["buckets"]) == 10


def test_cli_report_filter_by_kind(tmp_path: Path) -> None:
    """The CLI ``--kind`` filter restricts to a single decision_kind."""
    log = tmp_path / "calibration.jsonl"
    _log_batch(log, n=20)
    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["calibration", "report", "--log-path", str(log), "--kind", "judge"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # 20 records, every other is "judge" -> 10.
    assert payload["decisions"] == 10
    assert payload["decision_kind"] == "judge"


def test_cli_report_writes_to_output_file(tmp_path: Path) -> None:
    """The ``--output`` flag persists the report JSON to disk."""
    log = tmp_path / "calibration.jsonl"
    _log_batch(log, n=10)
    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["calibration", "report", "--log-path", str(log), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    parsed = json.loads(out.read_text())
    assert parsed["decisions"] == 10


def test_cli_report_rejects_invalid_duration(tmp_path: Path) -> None:
    """An invalid duration spec surfaces a clear error and non-zero exit."""
    log = tmp_path / "calibration.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["calibration", "report", "--log-path", str(log), "--since", "weasel"],
    )
    assert result.exit_code != 0
    # The error message originates from ``parse_duration``.
    assert "duration" in str(result.exception) or "duration" in result.output


def test_cli_report_custom_bin_count(tmp_path: Path) -> None:
    """The ``--bins`` option changes the number of reliability buckets."""
    log = tmp_path / "calibration.jsonl"
    _log_batch(log, n=20)
    runner = CliRunner()
    result = runner.invoke(
        eval_group,
        ["calibration", "report", "--log-path", str(log), "--bins", "5"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["buckets"]) == 5
