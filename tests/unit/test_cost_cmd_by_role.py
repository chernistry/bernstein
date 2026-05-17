"""CLI tests for ``bernstein cost --by role|feature_label`` (issue #1320)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from bernstein.cli.cost import cost_cmd
from click.testing import CliRunner

from bernstein.cli.run_bootstrap import _parse_budget_spec
from bernstein.core.cost.spend_ledger import CallTags, SpendLedger

# ---------------------------------------------------------------------------
# --budget / --hard-budget spec parser
# ---------------------------------------------------------------------------


class TestBudgetSpec:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("5usd", 5.0),
            ("$5", 5.0),
            ("5.0", 5.0),
            ("5.5usd", 5.5),
            ("$5.5", 5.5),
            ("10.25", 10.25),
        ],
    )
    def test_accepts_common_formats(self, raw: str, expected: float) -> None:
        assert _parse_budget_spec(raw) == pytest.approx(expected)

    def test_negative_clamped_to_zero(self) -> None:
        assert _parse_budget_spec("-1.0") == 0.0

    def test_blank_returns_none(self) -> None:
        assert _parse_budget_spec(None) is None
        assert _parse_budget_spec("") is None
        assert _parse_budget_spec("   ") is None

    def test_invalid_raises(self) -> None:
        import click

        with pytest.raises(click.BadParameter):
            _parse_budget_spec("five dollars")


# ---------------------------------------------------------------------------
# cost CLI: --by role|feature_label backed by the ledger
# ---------------------------------------------------------------------------


@pytest.fixture()
def sdd_with_ledger(tmp_path: Path) -> Path:
    """Build a ``.sdd`` tree with a populated spend ledger."""
    sdd = tmp_path / ".sdd"
    metrics = sdd / "metrics"
    cost = sdd / "cost"
    metrics.mkdir(parents=True)
    cost.mkdir(parents=True)

    # Minimal tasks.jsonl so the CLI doesn't bail on "no metrics".
    now = time.time()
    (metrics / "tasks.jsonl").write_text(
        json.dumps(
            {
                "task_id": "t-1",
                "role": "backend",
                "model": "claude-sonnet-4",
                "timestamp": now,
                "tokens_prompt": 100,
                "tokens_completion": 50,
                "cost_usd": 0.05,
                "agent_id": "a-1",
            }
        )
    )

    led = SpendLedger(path=cost / "ledger.jsonl", run_id="r-1")
    led.record(tags=CallTags(task_id="t-1", agent_id="a-1", role="backend"), model="sonnet", cost_usd=0.10)
    led.record(tags=CallTags(task_id="t-2", agent_id="a-1", role="backend"), model="sonnet", cost_usd=0.20)
    led.record(
        tags=CallTags(task_id="t-3", agent_id="a-2", role="qa", feature_label="rag"), model="haiku", cost_usd=0.05
    )
    return sdd


class TestCostByRole:
    def test_by_role_uses_ledger(self, sdd_with_ledger: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cost_cmd,
            [
                "--metrics-dir",
                str(sdd_with_ledger / "metrics"),
                "--ledger",
                str(sdd_with_ledger / "cost" / "ledger.jsonl"),
                "--by",
                "role",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["grouped_by"] == "role"
        grouped = data["grouped"]
        assert grouped["backend"]["cost_usd"] == pytest.approx(0.30)
        assert grouped["qa"]["cost_usd"] == pytest.approx(0.05)

    def test_by_feature_label_uses_ledger(self, sdd_with_ledger: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cost_cmd,
            [
                "--metrics-dir",
                str(sdd_with_ledger / "metrics"),
                "--ledger",
                str(sdd_with_ledger / "cost" / "ledger.jsonl"),
                "--by",
                "feature_label",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        grouped = data["grouped"]
        assert grouped["rag"]["cost_usd"] == pytest.approx(0.05)
        assert "unknown" in grouped

    def test_since_today_alias(self, sdd_with_ledger: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cost_cmd,
            [
                "--metrics-dir",
                str(sdd_with_ledger / "metrics"),
                "--since",
                "today",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["time_range"] == "last 24h"

    def test_by_role_falls_back_to_tasks_when_ledger_missing(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        metrics = sdd / "metrics"
        metrics.mkdir(parents=True)
        now = time.time()
        rows = [
            {
                "task_id": f"t{i}",
                "role": "backend" if i < 2 else "qa",
                "model": "sonnet",
                "timestamp": now,
                "cost_usd": 0.10,
                "agent_id": "a",
            }
            for i in range(3)
        ]
        (metrics / "tasks.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

        runner = CliRunner()
        result = runner.invoke(
            cost_cmd,
            [
                "--metrics-dir",
                str(metrics),
                "--ledger",
                str(sdd / "cost" / "ledger.jsonl"),  # absent
                "--by",
                "role",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        grouped = data["grouped"]
        assert grouped["backend"]["cost_usd"] == pytest.approx(0.20)
        assert grouped["qa"]["cost_usd"] == pytest.approx(0.10)
