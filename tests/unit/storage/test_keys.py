"""Unit tests for canonical key helpers (oai-003)."""

from __future__ import annotations

from bernstein.core.storage.keys import (
    audit_log_key,
    checkpoint_key,
    cost_ledger_key,
    metrics_dump_key,
    rotated_audit_key,
    state_key,
    task_output_key,
    task_progress_key,
    uncommitted_index_key,
    wal_closed_marker_key,
    wal_key,
)


def test_wal_key_shape() -> None:
    assert wal_key("run-1") == "runtime/wal/run-1.wal.jsonl"


def test_wal_closed_marker_shape() -> None:
    assert wal_closed_marker_key("run-1") == "runtime/wal/run-1.wal.closed"


def test_uncommitted_index_shape() -> None:
    assert uncommitted_index_key() == "runtime/wal/uncommitted.idx.json"


def test_checkpoint_key_shape() -> None:
    assert checkpoint_key("run-1", "ck-5") == "runtime/checkpoints/run-1/ck-5.json"


def test_state_key_shape() -> None:
    assert state_key() == "runtime/state.json"


def test_task_output_key_shape() -> None:
    assert task_output_key("t-42") == "tasks/t-42/output.json"


def test_task_progress_key_shape() -> None:
    assert task_progress_key("t-42") == "tasks/t-42/progress.jsonl"


def test_audit_log_key_shape() -> None:
    assert audit_log_key("2026-04-19") == "audit/2026-04-19.jsonl"


def test_rotated_audit_key_shape() -> None:
    assert rotated_audit_key("2026-04-19") == "audit/2026-04-19.jsonl.gz"


def test_metrics_dump_key_shape() -> None:
    assert metrics_dump_key("r-1", "1713567890") == "metrics/r-1/1713567890.json"


def test_cost_ledger_key_shape() -> None:
    assert cost_ledger_key("r-1") == "cost/r-1/ledger.jsonl"
