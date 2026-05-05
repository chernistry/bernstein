"""Tests for the per-(model, effort, phase) rework-rate ledger.

Covers ledger round-trip, atomic concurrent appends, the windowing
behaviour, and shard isolation between buckets.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from bernstein.core.routing.rework_ledger import (
    ReworkLedger,
    ReworkSample,
    _bucket_key,
    _shard_name,
    default_ledger,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Bucket-key fingerprinting
# ---------------------------------------------------------------------------


class TestBucketKey:
    def test_key_lowercases(self) -> None:
        assert _bucket_key("Sonnet", "Normal", "Implement") == "sonnet|normal|implement"

    def test_distinct_buckets_distinct_shards(self) -> None:
        a = _shard_name("sonnet", "normal", "implement")
        b = _shard_name("sonnet", "normal", "review")
        c = _shard_name("opus", "normal", "implement")
        assert a != b
        assert a != c
        assert b != c

    def test_shard_is_deterministic(self) -> None:
        assert _shard_name("sonnet", "normal", "implement") == _shard_name("sonnet", "normal", "implement")


# ---------------------------------------------------------------------------
# Round-trip: write then read
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_records_sample_and_reads_it_back(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        ledger.record(model="sonnet", effort="normal", phase="implement", outcome="success")
        ledger.record(model="sonnet", effort="normal", phase="implement", outcome="rework")

        rate = ledger.rework_rate(model="sonnet", effort="normal", phase="implement")
        assert rate.samples == 2
        assert rate.rework == 1
        assert rate.rate == 0.5

    def test_empty_bucket_returns_zero(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        rate = ledger.rework_rate(model="opus", effort="max", phase="implement")
        assert rate.samples == 0
        assert rate.rework == 0
        assert rate.rate == 0.0

    def test_buckets_are_isolated(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        for _ in range(5):
            ledger.record(model="sonnet", effort="normal", phase="implement", outcome="rework")
        for _ in range(5):
            ledger.record(model="opus", effort="max", phase="implement", outcome="success")

        sonnet_rate = ledger.rework_rate(model="sonnet", effort="normal", phase="implement")
        opus_rate = ledger.rework_rate(model="opus", effort="max", phase="implement")
        assert sonnet_rate.rate == 1.0
        assert opus_rate.rate == 0.0

    def test_persisted_lines_are_valid_json(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        ledger.record(
            model="sonnet",
            effort="normal",
            phase="implement",
            outcome="rework",
            triggered_by="verifier",
        )
        shard = next((tmp_path / "rework").iterdir())
        for line in shard.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            assert payload["model"] == "sonnet"
            assert payload["outcome"] == "rework"
            assert payload["triggered_by"] == "verifier"
            assert isinstance(payload["ts"], (int, float))


# ---------------------------------------------------------------------------
# Windowed reads
# ---------------------------------------------------------------------------


class TestWindow:
    def test_window_excludes_old_samples(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        # Far-past sample: shouldn't be counted with a 1h window
        ledger.record(model="sonnet", effort="normal", phase="implement", outcome="rework", ts=1.0)
        # Fresh sample
        ledger.record(model="sonnet", effort="normal", phase="implement", outcome="success")

        rate = ledger.rework_rate(
            model="sonnet",
            effort="normal",
            phase="implement",
            window_hours=1.0,
        )
        assert rate.samples == 1
        assert rate.rework == 0

    def test_no_window_includes_everything(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        ledger.record(model="sonnet", effort="normal", phase="implement", outcome="rework", ts=1.0)
        ledger.record(model="sonnet", effort="normal", phase="implement", outcome="success")
        rate = ledger.rework_rate(model="sonnet", effort="normal", phase="implement")
        assert rate.samples == 2


# ---------------------------------------------------------------------------
# Atomic concurrent append
# ---------------------------------------------------------------------------


class TestConcurrentAppend:
    def test_no_records_lost_under_threading(self, tmp_path: Path) -> None:
        ledger = ReworkLedger(root=tmp_path / "rework")
        n_threads = 8
        per_thread = 50

        def worker() -> None:
            for _ in range(per_thread):
                ledger.record(
                    model="sonnet",
                    effort="normal",
                    phase="implement",
                    outcome="rework",
                )

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rate = ledger.rework_rate(model="sonnet", effort="normal", phase="implement")
        assert rate.samples == n_threads * per_thread
        # Every record was 'rework'
        assert rate.rework == n_threads * per_thread

    def test_concurrent_append_lines_are_intact(self, tmp_path: Path) -> None:
        """Each line in the shard parses cleanly, i.e. no torn writes."""
        ledger = ReworkLedger(root=tmp_path / "rework")

        def worker() -> None:
            for _ in range(100):
                ledger.record(model="sonnet", effort="normal", phase="implement", outcome="success")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        shard = next((tmp_path / "rework").iterdir())
        lines = shard.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 4 * 100
        for line in lines:
            payload = json.loads(line)  # would raise if torn
            assert payload["outcome"] == "success"


# ---------------------------------------------------------------------------
# Default singleton
# ---------------------------------------------------------------------------


class TestDefaultLedger:
    def test_default_singleton_per_workdir(self, tmp_path: Path) -> None:
        a = default_ledger(tmp_path)
        b = default_ledger(tmp_path)
        assert a is b

    def test_default_root_under_sdd_runtime(self, tmp_path: Path) -> None:
        ledger = default_ledger(tmp_path)
        assert ledger.root == tmp_path / ".sdd" / "runtime" / "rework"


# ---------------------------------------------------------------------------
# Sample dataclass invariants
# ---------------------------------------------------------------------------


class TestReworkSample:
    def test_frozen(self) -> None:
        s = ReworkSample(model="sonnet", effort="normal", phase="implement", outcome="success", ts=1.0)
        try:
            s.model = "opus"  # type: ignore[misc]
        except Exception:
            return  # frozen, expected
        msg = "ReworkSample is not frozen"
        raise AssertionError(msg)
