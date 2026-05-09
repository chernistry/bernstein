"""Unit tests for ``bernstein.evals.driver``.

Covers the JSON-output shape — the contract consumed by the (deferred)
``/eval/leaderboard`` route, the public ``bernstein-eval`` repo, and the
Stanford Terminal-Bench 2.0 submission script. Keeping the shape stable is
load-bearing; bumping ``schema_version`` is a deliberate breaking change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.evals.driver import (
    LeaderboardEntry,
    main,
    render_badge_markdown,
    run_single_task,
    synthetic_smoke_entry,
    write_leaderboard_entry,
)

# Required keys on the public JSON contract. Adding a key is non-breaking;
# removing or renaming one requires bumping ``schema_version``.
_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "suite",
        "task_id",
        "harness_name",
        "harness_version",
        "model",
        "model_version",
        "resolved",
        "cost_usd",
        "duration_seconds",
        "run_at",
        "extra",
    },
)


@pytest.fixture()
def entry() -> LeaderboardEntry:
    """A minimal entry against ``swe-bench-pro``.

    Returns:
        A deterministic :class:`LeaderboardEntry` for shape assertions.
    """
    return run_single_task(
        suite="swe-bench-pro",
        task_id="django__django-11905",
        model="claude-opus-4-7",
        model_version="20260101",
        resolved=True,
        cost_usd=0.42,
        duration_seconds=58.3,
        extra={"language": "python", "repo_visibility": "public"},
        run_at="2026-05-07T00:00:00+00:00",
    )


def test_to_dict_has_full_contract(entry: LeaderboardEntry) -> None:
    """Every documented field must appear in the serialised dict."""
    payload = entry.to_dict()

    assert set(payload) == _REQUIRED_KEYS
    assert payload["schema_version"] == "1"
    assert payload["suite"] == "swe-bench-pro"
    assert payload["task_id"] == "django__django-11905"
    assert payload["harness_name"] == "bernstein"
    assert payload["model"] == "claude-opus-4-7"
    assert payload["model_version"] == "20260101"
    assert payload["resolved"] is True
    assert payload["cost_usd"] == pytest.approx(0.42)
    assert payload["duration_seconds"] == pytest.approx(58.3)
    assert payload["run_at"] == "2026-05-07T00:00:00+00:00"
    assert payload["extra"] == {"language": "python", "repo_visibility": "public"}
    assert payload["harness_version"], "harness_version must not be empty"


def test_to_dict_round_trips_through_json(entry: LeaderboardEntry) -> None:
    """Serialise / deserialise via JSON to catch non-stdlib types."""
    blob = json.dumps(entry.to_dict(), sort_keys=True)
    decoded = json.loads(blob)
    assert decoded["task_id"] == "django__django-11905"
    assert decoded["resolved"] is True
    # Dict round-trip must preserve every required key.
    assert set(decoded) == _REQUIRED_KEYS


def test_fingerprint_is_stable_and_pair_dependent(entry: LeaderboardEntry) -> None:
    """Fingerprint must depend on the harness-model pair, not on outcome."""
    same_pair_other_outcome = run_single_task(
        suite=entry.suite,
        task_id=entry.task_id,
        model=entry.model,
        model_version=entry.model_version,
        resolved=False,
        run_at=entry.run_at,
    )
    different_model = run_single_task(
        suite=entry.suite,
        task_id=entry.task_id,
        model="codex-gpt-5-5",
        model_version="20260101",
        resolved=True,
        run_at=entry.run_at,
    )
    assert entry.fingerprint() == same_pair_other_outcome.fingerprint()
    assert entry.fingerprint() != different_model.fingerprint()
    # Hex digest of fixed length, lowercase — required by the dedup contract.
    fp = entry.fingerprint()
    assert len(fp) == 64
    assert fp == fp.lower()


def test_write_leaderboard_entry_appends_jsonl(
    entry: LeaderboardEntry,
    tmp_path: Path,
) -> None:
    """JSONL writer creates the dir, appends, and round-trips."""
    out_dir = tmp_path / "swe-bench-pro"
    first = write_leaderboard_entry(entry, out_dir)
    second = write_leaderboard_entry(entry, out_dir)
    assert first == second
    assert first.name == "swe-bench-pro.jsonl"
    lines = first.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    decoded = [json.loads(line) for line in lines]
    assert all(d["task_id"] == entry.task_id for d in decoded)


def test_render_badge_markdown_orders_suites() -> None:
    """Badge renders all three suites in the canonical narrative order."""
    entries = [
        run_single_task(
            suite="swe-rebench",
            task_id="t1",
            model="m",
            model_version="v",
            resolved=True,
            run_at="2026-05-07T00:00:00+00:00",
        ),
        run_single_task(
            suite="swe-bench-pro",
            task_id="t2",
            model="m",
            model_version="v",
            resolved=False,
            run_at="2026-05-07T00:00:00+00:00",
        ),
        run_single_task(
            suite="terminal-bench-2",
            task_id="t3",
            model="m",
            model_version="v",
            resolved=True,
            run_at="2026-05-07T00:00:00+00:00",
        ),
    ]
    md = render_badge_markdown(entries)
    pro_idx = md.index("swe-bench-pro")
    term_idx = md.index("terminal-bench-2")
    rebench_idx = md.index("swe-rebench")
    assert pro_idx < term_idx < rebench_idx


def test_render_badge_markdown_handles_empty() -> None:
    """Empty input returns a placeholder, not an exception."""
    out = render_badge_markdown([])
    assert "no contamination-resistant" in out.lower()


def test_synthetic_smoke_entry_is_deterministic() -> None:
    """The smoke fixture is deterministic so CI snapshots stay stable."""
    a = synthetic_smoke_entry()
    b = synthetic_smoke_entry()
    assert a.to_dict() == b.to_dict()
    assert a.suite == "swe-bench-pro"
    assert a.model == "synthetic"


def test_main_writes_jsonl_and_badge(tmp_path: Path) -> None:
    """The CLI entry point used by the nightly workflow writes both files."""
    out_dir = tmp_path / "eval"
    badge_path = tmp_path / "badge.md"
    rc = main(["--out", str(out_dir), "--badge", str(badge_path)])
    assert rc == 0
    jsonl = out_dir / "swe-bench-pro" / "swe-bench-pro.jsonl"
    assert jsonl.exists()
    payload = json.loads(jsonl.read_text(encoding="utf-8").strip().splitlines()[0])
    assert set(payload) == _REQUIRED_KEYS
    assert badge_path.exists()
    assert "swe-bench-pro" in badge_path.read_text(encoding="utf-8")
