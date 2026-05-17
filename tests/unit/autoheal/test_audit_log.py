"""Unit tests for ``bernstein.core.autoheal.audit_log``."""

from __future__ import annotations

import json
from pathlib import Path

from bernstein.core.autoheal.audit_log import (
    HealRecord,
    _coerce_outcome,
    append,
    iter_records,
    now_record,
)


def _mk(**overrides: object) -> HealRecord:
    base: dict[str, object] = dict(
        ts=1700000000.0,
        run_id="run-1",
        head_sha="abc123def456",
        strategy="ruff-format",
        cls="safe",
        confidence=0.85,
        outcome="applied",
        cost_usd=0.0,
        llm_calls=0,
        patch_sha="dead",
        decision_id="dec-1",
        rationale="ok",
    )
    base.update(overrides)
    return HealRecord(**base)  # type: ignore[arg-type]


def test_to_jsonl_is_valid_compact_json() -> None:
    rec = _mk()
    line = rec.to_jsonl()
    parsed = json.loads(line)
    assert parsed["ts"] == 1700000000.0
    assert parsed["run_id"] == "run-1"
    assert parsed["outcome"] == "applied"
    assert "\n" not in line


def test_append_creates_parent_dir(tmp_path: Path) -> None:
    p = tmp_path / "missing" / "deep" / "hist.jsonl"
    append(_mk(), p)
    assert p.exists()
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_append_is_idempotent_per_call(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    for _ in range(3):
        append(_mk(), p)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def test_iter_records_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    append(_mk(run_id="r1"), p)
    append(_mk(run_id="r2"), p)
    parsed = list(iter_records(p))
    assert [r.run_id for r in parsed] == ["r1", "r2"]


def test_iter_records_skips_blank_lines_and_garbage(tmp_path: Path) -> None:
    p = tmp_path / "h.jsonl"
    p.write_text(
        _mk(run_id="r1").to_jsonl() + "\n\nnot-json\n" + _mk(run_id="r2").to_jsonl() + "\n",
        encoding="utf-8",
    )
    parsed = list(iter_records(p))
    assert [r.run_id for r in parsed] == ["r1", "r2"]


def test_iter_records_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(iter_records(tmp_path / "absent.jsonl")) == []


def test_coerce_outcome_known_value() -> None:
    assert _coerce_outcome("applied") == "applied"
    assert _coerce_outcome("shadow") == "shadow"


def test_coerce_outcome_unknown_value_is_default() -> None:
    assert _coerce_outcome("not-a-real-outcome") == "skipped_no_jobs"
    assert _coerce_outcome(123) == "skipped_no_jobs"
    assert _coerce_outcome(None) == "skipped_no_jobs"


def test_now_record_has_recent_ts() -> None:
    rec = now_record(
        run_id="r",
        head_sha="s",
        strategy="x",
        cls="safe",
        confidence=0.5,
        outcome="applied",
    )
    import time

    assert abs(rec.ts - time.time()) < 5.0


def test_jsonl_keys_are_sorted() -> None:
    rec = _mk()
    line = rec.to_jsonl()
    # Keys should appear in alphabetical order in the serialised line.
    keys: list[str] = []
    parsed = json.loads(line)
    keys = list(parsed.keys())
    assert keys == sorted(keys)
