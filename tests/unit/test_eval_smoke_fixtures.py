"""Presence test for committed golden/smoke fixtures.

Guards against regression of the bug where ``bernstein eval run --tier smoke``
exited 1 with "No golden tasks found" because no fixture markdown files were
seeded under ``.sdd/eval/golden/smoke/``.
"""

from __future__ import annotations

from pathlib import Path

from bernstein.eval.golden import load_golden_tasks

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SMOKE_DIR = _REPO_ROOT / ".sdd" / "eval" / "golden" / "smoke"


def test_smoke_fixture_directory_exists() -> None:
    assert _SMOKE_DIR.is_dir(), f"smoke fixture dir missing: {_SMOKE_DIR}"


def test_smoke_fixture_has_at_least_one_md_file() -> None:
    md_files = sorted(_SMOKE_DIR.glob("*.md"))
    assert md_files, f"no smoke .md fixtures under {_SMOKE_DIR}"


def test_smoke_fixtures_parse_cleanly() -> None:
    tasks = load_golden_tasks(_REPO_ROOT / ".sdd" / "eval" / "golden", tier_filter="smoke")
    assert tasks, "load_golden_tasks returned empty for smoke tier"
    for task in tasks:
        assert task.tier == "smoke"
        assert task.id, f"task at {task} has empty id"
        assert task.title, f"task {task.id} has empty title"
        assert task.description, f"task {task.id} has empty description"
        assert task.max_duration_s > 0, f"task {task.id} has non-positive max_duration_s"
        assert task.max_cost_usd > 0, f"task {task.id} has non-positive max_cost_usd"
