"""Hypothesis property tests for the abandon primitive (#1350).

Invariants under test:
* Concurrent appends never lose rows.
* ``read_all`` round-trips every appended row exactly once.
* Status transitions never regress: an abandoned task can never reach
  DONE or CLOSED through the lifecycle transition table.
* Aggregation totals match the sum of individual row contributions.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from bernstein.core.tasks.abandon import (
    AbandonmentLedger,
    AbandonReason,
    new_abandonment,
)
from bernstein.core.tasks.lifecycle import TASK_TRANSITIONS
from bernstein.core.tasks.models import TaskStatus

_REASONS: list[AbandonReason] = list(AbandonReason)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _row_strategy() -> st.SearchStrategy[dict[str, Any]]:
    return st.fixed_dictionaries(
        {
            "task_id": st.text(alphabet="ABCDEFGHIJ0123456789-", min_size=1, max_size=20),
            "reason": st.sampled_from(_REASONS),
            "detail": st.text(max_size=80),
            "role": st.sampled_from(["backend", "qa", "ops", "frontend", ""]),
            "adapter": st.sampled_from(["claude", "codex", "opencode", ""]),
            "cost_to_date_usd": st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
            "attempts": st.integers(min_value=0, max_value=10),
        }
    )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(rows=st.lists(_row_strategy(), min_size=0, max_size=25))
def test_append_then_read_round_trip(tmp_path_factory: Any, rows: list[dict[str, Any]]) -> None:
    """Every appended row must reappear exactly once in read_all output."""
    tmp = Path(tmp_path_factory.mktemp("ledger_rt"))
    ledger = AbandonmentLedger(tmp)
    inserted = []
    for fields in rows:
        row = new_abandonment(**fields)
        ledger.append(row)
        inserted.append(row)
    loaded = ledger.read_all()
    assert [r.id for r in loaded] == [r.id for r in inserted]
    assert {r.task_id for r in loaded} == {r.task_id for r in inserted}


@given(rows=st.lists(_row_strategy(), min_size=1, max_size=15))
def test_each_row_is_one_jsonl_line(tmp_path_factory: Any, rows: list[dict[str, Any]]) -> None:
    """Append must emit exactly one newline-terminated line per row."""
    tmp = Path(tmp_path_factory.mktemp("ledger_lines"))
    ledger = AbandonmentLedger(tmp)
    for fields in rows:
        ledger.append(new_abandonment(**fields))
    body = ledger.path.read_text(encoding="utf-8")
    assert body.count("\n") == len(rows)
    for line in body.splitlines():
        json.loads(line)  # each line is independently parseable


@given(rows=st.lists(_row_strategy(), min_size=0, max_size=20))
def test_stats_total_matches_row_count(tmp_path_factory: Any, rows: list[dict[str, Any]]) -> None:
    tmp = Path(tmp_path_factory.mktemp("ledger_stats"))
    ledger = AbandonmentLedger(tmp)
    for fields in rows:
        ledger.append(new_abandonment(**fields))
    assert ledger.stats()["total"] == len(rows)


@given(rows=st.lists(_row_strategy(), min_size=0, max_size=20))
def test_stats_by_reason_sums_match_row_counts(tmp_path_factory: Any, rows: list[dict[str, Any]]) -> None:
    tmp = Path(tmp_path_factory.mktemp("ledger_reason_sum"))
    ledger = AbandonmentLedger(tmp)
    for fields in rows:
        ledger.append(new_abandonment(**fields))
    by_reason = ledger.stats()["by_reason"]
    expected: dict[str, int] = {}
    for fields in rows:
        reason = fields["reason"].value
        expected[reason] = expected.get(reason, 0) + 1
    assert by_reason == expected


@given(rows=st.lists(_row_strategy(), min_size=1, max_size=15))
def test_list_recent_sorted_newest_first(tmp_path_factory: Any, rows: list[dict[str, Any]]) -> None:
    tmp = Path(tmp_path_factory.mktemp("ledger_recent"))
    ledger = AbandonmentLedger(tmp)
    for fields in rows:
        ledger.append(new_abandonment(**fields))
    recent = ledger.list_recent(limit=len(rows))
    timestamps = [r.timestamp for r in recent]
    assert timestamps == sorted(timestamps, reverse=True)


@given(
    completions=st.dictionaries(
        keys=st.sampled_from(["backend", "qa", "ops"]),
        values=st.integers(min_value=0, max_value=1000),
        max_size=3,
    ),
    rows=st.lists(_row_strategy(), min_size=0, max_size=10),
)
def test_abandon_rate_by_role_in_unit_interval(
    tmp_path_factory: Any, completions: dict[str, int], rows: list[dict[str, Any]]
) -> None:
    tmp = Path(tmp_path_factory.mktemp("ledger_rate"))
    ledger = AbandonmentLedger(tmp)
    for fields in rows:
        ledger.append(new_abandonment(**fields))
    rates = ledger.abandon_rate_by_role(completions)
    for rate in rates.values():
        assert 0.0 <= rate <= 1.0


@given(rows=st.lists(_row_strategy(), min_size=2, max_size=20))
def test_ids_are_unique_when_constructed_via_factory(tmp_path_factory: Any, rows: list[dict[str, Any]]) -> None:
    tmp = Path(tmp_path_factory.mktemp("ledger_uniq"))
    ledger = AbandonmentLedger(tmp)
    for fields in rows:
        ledger.append(new_abandonment(**fields))
    loaded = ledger.read_all()
    assert len({r.id for r in loaded}) == len(loaded)


# ---------------------------------------------------------------------------
# Status transitions never regress
# ---------------------------------------------------------------------------


@given(target=st.sampled_from([s for s in TaskStatus if s is not TaskStatus.ABANDONED]))
def test_abandoned_is_terminal_for_all_other_states(target: TaskStatus) -> None:
    """ABANDONED is a closed terminal status - no outbound transitions."""
    assert (TaskStatus.ABANDONED, target) not in TASK_TRANSITIONS


@given(target=st.sampled_from([TaskStatus.DONE, TaskStatus.CLOSED]))
def test_abandoned_can_never_reach_completed_states(target: TaskStatus) -> None:
    """Critical: an abandoned task must never become DONE or CLOSED."""
    assert (TaskStatus.ABANDONED, target) not in TASK_TRANSITIONS


# ---------------------------------------------------------------------------
# Concurrent appends from multiple threads
# ---------------------------------------------------------------------------


@given(
    writers=st.integers(min_value=2, max_value=6),
    per_writer=st.integers(min_value=1, max_value=15),
)
def test_concurrent_appends_no_loss(tmp_path_factory: Any, writers: int, per_writer: int) -> None:
    tmp = Path(tmp_path_factory.mktemp("ledger_conc"))
    ledger = AbandonmentLedger(tmp)

    def worker(idx: int) -> None:
        for i in range(per_writer):
            ledger.append(
                new_abandonment(
                    task_id=f"W{idx}-T{i}",
                    reason=AbandonReason.OTHER,
                )
            )

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = ledger.read_all()
    assert len(rows) == writers * per_writer
    assert len({row.id for row in rows}) == writers * per_writer
