"""Unit tests for :class:`AbandonmentLedger` persistence (#1350)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from bernstein.core.tasks.abandon import (
    LEDGER_FILENAME,
    AbandonmentLedger,
    AbandonReason,
    new_abandonment,
)


def _row(task_id: str = "T-1", reason: AbandonReason = AbandonReason.OUT_OF_SCOPE, **kwargs: object) -> object:
    return new_abandonment(task_id=task_id, reason=reason, **kwargs)  # type: ignore[arg-type]


class TestAbandonmentLedgerBasics:
    def test_path_resolves_under_runtime(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        assert ledger.path == tmp_path / "runtime" / LEDGER_FILENAME

    def test_read_empty_returns_empty_list(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        assert ledger.read_all() == []

    def test_list_recent_empty_returns_empty(self, tmp_path: Path) -> None:
        assert AbandonmentLedger(tmp_path).list_recent() == []

    def test_list_recent_with_zero_limit_returns_empty(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(_row())  # type: ignore[arg-type]
        assert ledger.list_recent(limit=0) == []

    def test_append_creates_parent_directory(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        assert not (tmp_path / "runtime").exists()
        ledger.append(_row())  # type: ignore[arg-type]
        assert ledger.path.exists()
        assert ledger.path.parent.exists()

    def test_append_writes_single_line_per_row(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(_row(task_id="T-1"))  # type: ignore[arg-type]
        ledger.append(_row(task_id="T-2"))  # type: ignore[arg-type]
        ledger.append(_row(task_id="T-3"))  # type: ignore[arg-type]
        body = ledger.path.read_text(encoding="utf-8")
        assert body.count("\n") == 3

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(_row(task_id="T-1"))  # type: ignore[arg-type]
        ledger.append(_row(task_id="T-2"))  # type: ignore[arg-type]
        for line in ledger.path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            assert "task_id" in data
            assert "reason" in data

    def test_append_does_not_rewrite_existing_rows(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(_row(task_id="T-1"))  # type: ignore[arg-type]
        first_size = ledger.path.stat().st_size
        ledger.append(_row(task_id="T-2"))  # type: ignore[arg-type]
        second_size = ledger.path.stat().st_size
        assert second_size > first_size

    def test_round_trip_preserves_all_fields(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        original = new_abandonment(
            task_id="T-99",
            reason=AbandonReason.BUDGET_EXCEEDED,
            detail="cap hit",
            role="qa",
            agent_id="sess",
            adapter="codex",
            cost_to_date_usd=1.5,
            attempts=2,
        )
        ledger.append(original)
        loaded = ledger.read_all()
        assert len(loaded) == 1
        assert loaded[0] == original


class TestAbandonmentLedgerReadAll:
    def test_read_skips_blank_lines(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        ledger.path.write_text("\n\n\n", encoding="utf-8")
        assert ledger.read_all() == []

    def test_read_skips_malformed_json(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(_row(task_id="T-1"))  # type: ignore[arg-type]
        # Append garbage
        with ledger.path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write('{"task_id": "T-2", "reason": "out_of_scope"}\n')
        rows = ledger.read_all()
        assert {row.task_id for row in rows} == {"T-1", "T-2"}

    def test_read_skips_unknown_reason_rows(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        with ledger.path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "a", "task_id": "T-1", "reason": "alien_value"}) + "\n")
            fh.write(json.dumps({"id": "b", "task_id": "T-2", "reason": "other"}) + "\n")
        rows = ledger.read_all()
        assert [r.task_id for r in rows] == ["T-2"]

    def test_read_skips_rows_missing_required_fields(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        with ledger.path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "a", "reason": "other"}) + "\n")  # no task_id
            fh.write(json.dumps({"id": "b", "task_id": "T-1"}) + "\n")  # no reason
            fh.write(json.dumps({"id": "c", "task_id": "T-2", "reason": "other"}) + "\n")
        rows = ledger.read_all()
        assert [r.task_id for r in rows] == ["T-2"]

    def test_read_skips_non_object_lines(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        with ledger.path.open("w", encoding="utf-8") as fh:
            fh.write("[1,2,3]\n")
            fh.write('"a string"\n')
            fh.write(json.dumps({"task_id": "T-1", "reason": "other"}) + "\n")
        rows = ledger.read_all()
        assert [r.task_id for r in rows] == ["T-1"]

    def test_read_preserves_append_order(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        for i in range(5):
            ledger.append(_row(task_id=f"T-{i}"))  # type: ignore[arg-type]
        ids = [r.task_id for r in ledger.read_all()]
        assert ids == ["T-0", "T-1", "T-2", "T-3", "T-4"]


class TestAbandonmentLedgerListRecent:
    def test_recent_sorts_newest_first(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(new_abandonment(task_id="old", reason=AbandonReason.OTHER, timestamp=100.0))
        ledger.append(new_abandonment(task_id="new", reason=AbandonReason.OTHER, timestamp=200.0))
        ledger.append(new_abandonment(task_id="mid", reason=AbandonReason.OTHER, timestamp=150.0))
        recent = ledger.list_recent()
        assert [r.task_id for r in recent] == ["new", "mid", "old"]

    def test_recent_honours_limit(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        for i in range(10):
            ledger.append(new_abandonment(task_id=f"T-{i}", reason=AbandonReason.OTHER, timestamp=float(i)))
        assert len(ledger.list_recent(limit=3)) == 3

    def test_recent_negative_limit_returns_empty(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(_row())  # type: ignore[arg-type]
        assert ledger.list_recent(limit=-5) == []


class TestAbandonmentLedgerAggregations:
    def _seed(self, tmp_path: Path) -> AbandonmentLedger:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(
            new_abandonment(
                task_id="T-1",
                reason=AbandonReason.OUT_OF_SCOPE,
                role="backend",
                adapter="claude",
            )
        )
        ledger.append(
            new_abandonment(
                task_id="T-2",
                reason=AbandonReason.OUT_OF_SCOPE,
                role="backend",
                adapter="claude",
            )
        )
        ledger.append(
            new_abandonment(
                task_id="T-3",
                reason=AbandonReason.BUDGET_EXCEEDED,
                role="qa",
                adapter="codex",
            )
        )
        return ledger

    def test_stats_total(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        stats = ledger.stats()
        assert stats["total"] == 3

    def test_stats_by_reason(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        stats = ledger.stats()
        assert stats["by_reason"] == {"out_of_scope": 2, "budget_exceeded": 1}

    def test_stats_by_role(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        stats = ledger.stats()
        assert stats["by_role"] == {"backend": 2, "qa": 1}

    def test_stats_by_adapter(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        stats = ledger.stats()
        assert stats["by_adapter"] == {"claude": 2, "codex": 1}

    def test_stats_excludes_empty_role(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.OTHER, role=""))
        stats = ledger.stats()
        assert stats["by_role"] == {}

    def test_stats_excludes_empty_adapter(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.OTHER, adapter=""))
        stats = ledger.stats()
        assert stats["by_adapter"] == {}

    def test_count_by_task(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.OTHER))
        assert ledger.count_by_task("T-1") == 2
        assert ledger.count_by_task("T-3") == 1
        assert ledger.count_by_task("nope") == 0

    def test_by_reason_for_task(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.OUT_OF_SCOPE))
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.OUT_OF_SCOPE))
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.BUDGET_EXCEEDED))
        ledger.append(new_abandonment(task_id="T-2", reason=AbandonReason.OTHER))
        counts = ledger.by_reason("T-1")
        assert counts[AbandonReason.OUT_OF_SCOPE] == 2
        assert counts[AbandonReason.BUDGET_EXCEEDED] == 1
        assert counts.get(AbandonReason.OTHER, 0) == 0

    def test_abandon_rate_by_role(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        rates = ledger.abandon_rate_by_role({"backend": 8, "qa": 1, "ops": 5})
        # backend: 2/(2+8)=0.2
        assert rates["backend"] == pytest.approx(0.2)
        # qa: 1/(1+1)=0.5
        assert rates["qa"] == pytest.approx(0.5)
        # ops: no abandons → 0
        assert rates["ops"] == 0.0

    def test_abandon_rate_by_role_no_completions(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        ledger.append(new_abandonment(task_id="T-1", reason=AbandonReason.OTHER, role="backend"))
        rates = ledger.abandon_rate_by_role({})
        assert rates["backend"] == 1.0

    def test_abandon_rate_by_adapter(self, tmp_path: Path) -> None:
        ledger = self._seed(tmp_path)
        rates = ledger.abandon_rate_by_adapter({"claude": 18, "codex": 4})
        # claude: 2/(2+18) = 0.1
        assert rates["claude"] == pytest.approx(0.1)
        # codex: 1/(1+4) = 0.2
        assert rates["codex"] == pytest.approx(0.2)

    def test_abandon_rate_handles_zero_denominator(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        # No abandons, no completions
        rates = ledger.abandon_rate_by_role({})
        assert rates == {}


class TestAbandonmentLedgerConcurrency:
    def test_threaded_appends_preserve_all_rows(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)
        n_writers = 8
        per_writer = 25

        def writer(writer_id: int) -> None:
            for i in range(per_writer):
                ledger.append(
                    new_abandonment(
                        task_id=f"W{writer_id}-T{i}",
                        reason=AbandonReason.OTHER,
                    )
                )

        threads = [threading.Thread(target=writer, args=(w,)) for w in range(n_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = ledger.read_all()
        assert len(rows) == n_writers * per_writer
        # IDs are globally unique
        assert len({r.id for r in rows}) == n_writers * per_writer

    def test_threaded_appends_produce_no_torn_lines(self, tmp_path: Path) -> None:
        ledger = AbandonmentLedger(tmp_path)

        def writer() -> None:
            for i in range(40):
                ledger.append(
                    new_abandonment(
                        task_id=f"T-{i}",
                        reason=AbandonReason.OTHER,
                        # Larger payloads stress the line-atomicity contract.
                        detail="x" * 256,
                    )
                )

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Every non-empty line must parse as JSON with our schema.
        for line in ledger.path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            assert "task_id" in data
            assert "reason" in data
