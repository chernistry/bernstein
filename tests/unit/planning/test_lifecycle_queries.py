"""Behavioral tests for PlanLifecycle queries and slug edge cases.

The existing planning/lifecycle suite focuses on archive transitions, hooks,
audit, and slug collisions. This file fills the query-helper and slug-edge
gaps: bucket creation, ``list_plans`` / ``find`` / ``bucket``, the non-dir
root guard, ``default_lifecycle``, and ``_slugify`` collapse/truncation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.planning.lifecycle import (
    PlanArchiveError,
    PlanLifecycle,
    PlanState,
    _slugify,
    default_lifecycle,
    is_archived_filename,
)

# ---------------------------------------------------------------------------
# _slugify edge cases
# ---------------------------------------------------------------------------


def test_slugify_normalizes_to_dash_lowercase() -> None:
    assert _slugify("My Cool Plan!") == "my-cool-plan"


def test_slugify_empty_collapses_to_plan() -> None:
    assert _slugify("   ") == "plan"


def test_slugify_symbols_only_collapses_to_plan() -> None:
    assert _slugify("!!!@@@###") == "plan"


def test_slugify_truncates_to_max_length() -> None:
    result = _slugify("x" * 100)
    assert len(result) == 60
    assert result == "x" * 60


def test_slugify_strips_trailing_dashes() -> None:
    assert _slugify("plan---") == "plan"


# ---------------------------------------------------------------------------
# is_archived_filename
# ---------------------------------------------------------------------------


def test_is_archived_filename_accepts_dated_hash_form() -> None:
    assert is_archived_filename("2026-05-22-my-plan-a1b2c3.yaml") is True


def test_is_archived_filename_rejects_plain_name() -> None:
    assert is_archived_filename("my-plan.yaml") is False


# ---------------------------------------------------------------------------
# PlanLifecycle construction + buckets
# ---------------------------------------------------------------------------


def test_lifecycle_creates_three_buckets(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    # The constructor creates all three buckets as a side effect.
    assert (lc.root / "active").is_dir()
    assert (lc.root / "completed").is_dir()
    assert (lc.root / "blocked").is_dir()


def test_lifecycle_bucket_maps_state_to_directory(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    assert lc.bucket(PlanState.ACTIVE).name == "active"
    assert lc.bucket(PlanState.COMPLETED).name == "completed"
    assert lc.bucket(PlanState.BLOCKED).name == "blocked"


def test_lifecycle_rejects_non_directory_root(tmp_path: Path) -> None:
    afile = tmp_path / "afile"
    afile.write_text("not a dir")
    with pytest.raises(PlanArchiveError, match="not a directory"):
        PlanLifecycle(afile)


# ---------------------------------------------------------------------------
# list_plans / find
# ---------------------------------------------------------------------------


def test_list_plans_empty_when_no_files(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    assert lc.list_plans() == []


def test_list_plans_finds_active_plan(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    (tmp_path / "plans" / "active" / "p1.yaml").write_text("name: p1\n")
    plans = lc.list_plans()
    assert len(plans) == 1
    assert plans[0].plan_id == "p1"
    assert plans[0].state is PlanState.ACTIVE


def test_list_plans_filtered_by_state(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    (tmp_path / "plans" / "active" / "p1.yaml").write_text("name: p1\n")
    (tmp_path / "plans" / "completed" / "p2.yaml").write_text("name: p2\n")
    active = lc.list_plans(PlanState.ACTIVE)
    assert [p.plan_id for p in active] == ["p1"]


def test_list_plans_sorted_alphabetically(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    for name in ("zebra", "alpha", "mike"):
        (tmp_path / "plans" / "active" / f"{name}.yaml").write_text("x: 1\n")
    ids = [p.plan_id for p in lc.list_plans(PlanState.ACTIVE)]
    assert ids == ["alpha", "mike", "zebra"]


def test_find_locates_plan_across_buckets(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    (tmp_path / "plans" / "blocked" / "stuck.yaml").write_text("name: stuck\n")
    found = lc.find("stuck")
    assert found is not None
    assert found.state is PlanState.BLOCKED
    assert found.plan_id == "stuck"


def test_find_returns_none_for_missing(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    assert lc.find("nonexistent") is None


# ---------------------------------------------------------------------------
# backfill_unmanaged
# ---------------------------------------------------------------------------


def test_backfill_moves_loose_yaml_into_active(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    (tmp_path / "plans" / "loose.yaml").write_text("name: loose\n")
    migrated = lc.backfill_unmanaged()
    assert len(migrated) == 1
    assert (tmp_path / "plans" / "active" / "loose.yaml").exists()
    assert not (tmp_path / "plans" / "loose.yaml").exists()


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    lc = PlanLifecycle(tmp_path / "plans")
    (tmp_path / "plans" / "loose.yaml").write_text("name: loose\n")
    lc.backfill_unmanaged()
    # Second run finds nothing loose to move.
    assert lc.backfill_unmanaged() == []


# ---------------------------------------------------------------------------
# default_lifecycle
# ---------------------------------------------------------------------------


def test_default_lifecycle_roots_under_plans(tmp_path: Path) -> None:
    lc = default_lifecycle(tmp_path)
    assert lc.root.name == "plans"
    assert lc.root.is_dir()
