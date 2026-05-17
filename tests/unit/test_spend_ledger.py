"""Tests for the rolling spend ledger (issue #1320).

Covers:
* :class:`SpendLedger.record` math and JSONL append semantics.
* Soft cap (warn at 80%, halt at 100%) and hard cap kill switch.
* Reroute hint via :meth:`SpendLedger.cheaper_model`.
* ``aggregate_entries`` group-by math for the ``bernstein cost --by``
  ``task|agent|role|model|feature_label|day`` dimensions.
* :class:`CostTracker` honouring ``hard_budget_usd`` and forwarding tags
  to a ledger.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bernstein.core.cost_tracker import CostTracker

from bernstein.core.cost.spend_ledger import (
    CallTags,
    LedgerEntry,
    SpendLedger,
    aggregate_entries,
)

# ---------------------------------------------------------------------------
# CallTags / LedgerEntry shape
# ---------------------------------------------------------------------------


class TestCallTags:
    def test_merged_drops_empty_fields(self) -> None:
        tags = CallTags(task_id="t-1", agent_id="", role="backend", feature_label="")
        merged = tags.merged()
        assert merged == {"task_id": "t-1", "role": "backend"}

    def test_merged_includes_extra(self) -> None:
        tags = CallTags(task_id="t-1", extra={"customer_id": "acme", "blank": ""})
        merged = tags.merged()
        assert merged["customer_id"] == "acme"
        assert "blank" not in merged

    def test_ledger_entry_roundtrip(self) -> None:
        entry = LedgerEntry(
            ts=1000.0,
            ts_iso="2026-05-17T00:00:00+00:00",
            run_id="r-1",
            task_id="t-1",
            agent_id="a-1",
            role="backend",
            feature_label="rag",
            model="sonnet",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
            cost_usd=0.123456,
            tags={"task_id": "t-1", "role": "backend"},
        )
        s = entry.to_json()
        restored = LedgerEntry.from_dict(json.loads(s))
        assert restored.task_id == "t-1"
        assert restored.cost_usd == pytest.approx(0.123456)
        assert restored.tags == {"task_id": "t-1", "role": "backend"}


# ---------------------------------------------------------------------------
# Record math + JSONL append
# ---------------------------------------------------------------------------


class TestRecord:
    def test_record_writes_one_line_per_call(self, tmp_path: Path) -> None:
        path = tmp_path / "ledger.jsonl"
        led = SpendLedger(path=path, run_id="r-1")
        for _ in range(3):
            led.record(
                tags=CallTags(task_id="t-1", agent_id="a-1", role="backend"),
                model="sonnet",
                cost_usd=0.10,
            )
        lines = path.read_text().splitlines()
        assert len(lines) == 3
        first = json.loads(lines[0])
        assert first["model"] == "sonnet"
        assert first["cost_usd"] == pytest.approx(0.10)
        assert first["task_id"] == "t-1"
        assert first["tags"]["role"] == "backend"

    def test_record_accumulates_totals(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", run_id="r-1")
        led.record(tags=CallTags(task_id="t-1", role="backend"), model="opus", cost_usd=0.40)
        led.record(tags=CallTags(task_id="t-1", role="backend"), model="opus", cost_usd=0.30)
        led.record(tags=CallTags(task_id="t-2", role="qa"), model="haiku", cost_usd=0.05)
        st = led.status()
        assert st.spent_usd == pytest.approx(0.75)
        by_task = led.totals_by("task")
        assert by_task["t-1"] == pytest.approx(0.70)
        assert by_task["t-2"] == pytest.approx(0.05)
        by_role = led.totals_by("role")
        assert by_role["backend"] == pytest.approx(0.70)
        assert by_role["qa"] == pytest.approx(0.05)

    def test_record_clamps_negative_cost(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl")
        st = led.record(tags=CallTags(task_id="t"), model="sonnet", cost_usd=-1.0)
        assert st.spent_usd == 0.0


# ---------------------------------------------------------------------------
# Soft + hard cap behaviour
# ---------------------------------------------------------------------------


class TestSoftCap:
    def test_no_cap_never_warns(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl")
        st = led.record(tags=CallTags(), model="opus", cost_usd=999.0)
        assert st.soft_warn is False
        assert st.soft_halt is False
        assert st.hard_halt is False

    def test_warn_at_80_pct(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", budget_usd=5.0)
        st = led.record(tags=CallTags(), model="sonnet", cost_usd=4.0)
        assert st.soft_warn is True
        assert st.soft_halt is False

    def test_no_warn_below_80_pct(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", budget_usd=5.0)
        st = led.record(tags=CallTags(), model="sonnet", cost_usd=3.9)
        assert st.soft_warn is False

    def test_halt_at_100_pct(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", budget_usd=5.0)
        st = led.record(tags=CallTags(), model="sonnet", cost_usd=5.0)
        assert st.soft_halt is True
        assert led.admits() is False

    def test_cheaper_model_after_warn(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", budget_usd=5.0)
        # Below 80% — no reroute
        led.record(tags=CallTags(), model="opus", cost_usd=1.0)
        assert led.cheaper_model("opus") is None
        # Cross the 80% line
        led.record(tags=CallTags(), model="opus", cost_usd=3.0)
        assert led.cheaper_model("opus") == "sonnet"
        assert led.cheaper_model("claude-sonnet-4") == "haiku"

    def test_warn_logged_once(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", budget_usd=5.0)
        with caplog.at_level("WARNING"):
            led.record(tags=CallTags(), model="opus", cost_usd=4.5)
            led.record(tags=CallTags(), model="opus", cost_usd=0.1)
        warn_lines = [r for r in caplog.records if "SOFT CAP WARN" in r.message]
        assert len(warn_lines) == 1


class TestHardCap:
    def test_hard_halt_blocks_admission(self, tmp_path: Path) -> None:
        led = SpendLedger(path=tmp_path / "ledger.jsonl", hard_budget_usd=10.0)
        led.record(tags=CallTags(), model="opus", cost_usd=10.0)
        st = led.status()
        assert st.hard_halt is True
        assert led.admits() is False

    def test_hard_halt_independent_of_soft(self, tmp_path: Path) -> None:
        """Hard cap trips even when soft budget is unset."""
        led = SpendLedger(path=tmp_path / "ledger.jsonl", hard_budget_usd=2.0)
        led.record(tags=CallTags(), model="sonnet", cost_usd=2.5)
        st = led.status()
        assert st.hard_halt is True
        assert st.soft_warn is False  # no soft budget configured

    def test_inverted_caps_clamp_soft(self, tmp_path: Path) -> None:
        """Soft > hard is meaningless; we clamp soft down to hard."""
        led = SpendLedger(
            path=tmp_path / "ledger.jsonl",
            budget_usd=20.0,
            hard_budget_usd=5.0,
        )
        assert led.budget_usd == 5.0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_aggregate_by_role(self) -> None:
        entries = [
            LedgerEntry(
                ts=0,
                ts_iso="",
                run_id="r",
                task_id="t1",
                agent_id="a1",
                role="backend",
                feature_label="",
                model="sonnet",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.10,
            ),
            LedgerEntry(
                ts=0,
                ts_iso="",
                run_id="r",
                task_id="t2",
                agent_id="a1",
                role="backend",
                feature_label="",
                model="sonnet",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.20,
            ),
            LedgerEntry(
                ts=0,
                ts_iso="",
                run_id="r",
                task_id="t3",
                agent_id="a2",
                role="qa",
                feature_label="",
                model="haiku",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.05,
            ),
        ]
        out = aggregate_entries(entries, "role")
        assert out["backend"]["cost_usd"] == pytest.approx(0.30)
        assert out["backend"]["calls"] == 2
        assert out["qa"]["cost_usd"] == pytest.approx(0.05)

    def test_aggregate_by_feature_label_uses_unknown_bucket(self) -> None:
        entries = [
            LedgerEntry(
                ts=0,
                ts_iso="",
                run_id="r",
                task_id="t1",
                agent_id="a1",
                role="backend",
                feature_label="rag",
                model="sonnet",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.10,
            ),
            LedgerEntry(
                ts=0,
                ts_iso="",
                run_id="r",
                task_id="t2",
                agent_id="a1",
                role="backend",
                feature_label="",
                model="sonnet",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.20,
            ),
        ]
        out = aggregate_entries(entries, "feature_label")
        assert out["rag"]["cost_usd"] == pytest.approx(0.10)
        assert out["unknown"]["cost_usd"] == pytest.approx(0.20)

    def test_aggregate_unknown_dimension_returns_empty(self) -> None:
        entries = [
            LedgerEntry(
                ts=0,
                ts_iso="",
                run_id="r",
                task_id="t",
                agent_id="a",
                role="r",
                feature_label="",
                model="m",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost_usd=0.10,
            ),
        ]
        assert aggregate_entries(entries, "bogus") == {}


# ---------------------------------------------------------------------------
# CostTracker integration: hard cap + tag propagation
# ---------------------------------------------------------------------------


class TestCostTrackerHardBudget:
    def test_hard_budget_trips_should_stop(self) -> None:
        tracker = CostTracker(run_id="r", hard_budget_usd=1.0)
        status = tracker.record("a-1", "t-1", "sonnet", 0, 0, cost_usd=1.5)
        assert status.should_stop is True
        assert tracker.can_spawn() is False

    def test_hard_budget_independent_of_soft(self) -> None:
        """should_stop trips on hard even when soft budget is unlimited."""
        tracker = CostTracker(run_id="r", budget_usd=0.0, hard_budget_usd=2.0)
        s1 = tracker.record("a", "t", "sonnet", 0, 0, cost_usd=1.0)
        assert s1.should_stop is False
        s2 = tracker.record("a", "t", "sonnet", 0, 0, cost_usd=1.5)
        assert s2.should_stop is True

    def test_record_propagates_tags_to_ledger(self, tmp_path: Path) -> None:
        ledger_path = tmp_path / "ledger.jsonl"
        ledger = SpendLedger(path=ledger_path, run_id="r-1", budget_usd=10.0)
        tracker = CostTracker(run_id="r-1", spend_ledger=ledger)
        tracker.record(
            "a-1",
            "t-1",
            "sonnet",
            100,
            50,
            cost_usd=0.05,
            role="backend",
            feature_label="rag",
        )
        lines = ledger_path.read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["task_id"] == "t-1"
        assert row["agent_id"] == "a-1"
        assert row["role"] == "backend"
        assert row["feature_label"] == "rag"
        assert row["cost_usd"] == pytest.approx(0.05)

    def test_cheaper_model_for_uses_ledger(self, tmp_path: Path) -> None:
        ledger = SpendLedger(path=tmp_path / "ledger.jsonl", budget_usd=5.0)
        tracker = CostTracker(run_id="r-1", spend_ledger=ledger)
        # Below 80% — no reroute
        tracker.record("a", "t", "opus", 0, 0, cost_usd=1.0)
        assert tracker.cheaper_model_for("opus") is None
        # Cross 80%
        tracker.record("a", "t", "opus", 0, 0, cost_usd=3.5)
        assert tracker.cheaper_model_for("opus") == "sonnet"

    def test_cheaper_model_for_without_ledger(self) -> None:
        """Fallback path uses local 80% check against tracker.budget_usd."""
        tracker = CostTracker(run_id="r-1", budget_usd=5.0)
        tracker.record("a", "t", "opus", 0, 0, cost_usd=1.0)
        assert tracker.cheaper_model_for("opus") is None
        tracker.record("a", "t", "opus", 0, 0, cost_usd=3.5)
        assert tracker.cheaper_model_for("opus") == "sonnet"

    def test_persistence_roundtrips_hard_budget(self, tmp_path: Path) -> None:
        tracker = CostTracker(run_id="r-1", budget_usd=5.0, hard_budget_usd=10.0)
        tracker.record("a", "t", "sonnet", 0, 0, cost_usd=0.5)
        tracker.save(tmp_path)
        restored = CostTracker.load(tmp_path, "r-1")
        assert restored is not None
        assert restored.hard_budget_usd == pytest.approx(10.0)
        assert restored.budget_usd == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Load entries from disk
# ---------------------------------------------------------------------------


class TestLoadEntries:
    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        out = SpendLedger.load_entries(tmp_path / "absent.jsonl")
        assert out == []

    def test_load_skips_malformed(self, tmp_path: Path) -> None:
        path = tmp_path / "ledger.jsonl"
        led = SpendLedger(path=path)
        led.record(tags=CallTags(task_id="t-1"), model="sonnet", cost_usd=0.10)
        # Inject a junk line
        with path.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
        led.record(tags=CallTags(task_id="t-2"), model="sonnet", cost_usd=0.20)

        entries = SpendLedger.load_entries(path)
        assert len(entries) == 2
        assert {e.task_id for e in entries} == {"t-1", "t-2"}
        assert sum(e.cost_usd for e in entries) == pytest.approx(0.30)
