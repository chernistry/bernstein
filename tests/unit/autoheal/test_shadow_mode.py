"""Unit tests for ``bernstein.core.autoheal.shadow_mode``."""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.autoheal.shadow_mode import (
    PROMOTION_THRESHOLD,
    ShadowRecord,
    ShadowState,
    load,
    save,
)


def test_default_status_is_shadow() -> None:
    r = ShadowRecord()
    assert r.status == "shadow"
    assert r.is_allowed_to_push() is False


def test_promotion_after_threshold_with_enough_wins() -> None:
    r = ShadowRecord()
    for _ in range(4):
        r.record(success=True)
    r.record(success=False)
    assert r.observations == PROMOTION_THRESHOLD
    assert r.status == "active"
    assert r.is_allowed_to_push() is True


def test_retirement_after_threshold_with_too_few_wins() -> None:
    r = ShadowRecord()
    r.record(success=True)
    for _ in range(4):
        r.record(success=False)
    assert r.status == "retired"
    assert r.is_allowed_to_push() is False


def test_review_when_results_are_middling() -> None:
    r = ShadowRecord()
    # 3 wins, 2 losses -> middling.
    for _ in range(3):
        r.record(success=True)
    for _ in range(2):
        r.record(success=False)
    assert r.status == "review"
    assert r.is_allowed_to_push() is False


def test_active_record_ignores_further_updates() -> None:
    r = ShadowRecord(status="active", observations=5, wins=5)
    r.record(success=False)
    # Status stays active; counters are not touched.
    assert r.status == "active"
    assert r.observations == 5


def test_retired_record_ignores_further_updates() -> None:
    r = ShadowRecord(status="retired", observations=5, losses=5)
    r.record(success=True)
    assert r.status == "retired"
    assert r.observations == 5


def test_state_ensure_creates_new() -> None:
    s = ShadowState()
    rec = s.ensure("new-strategy")
    assert rec.status == "shadow"
    assert "new-strategy" in s.strategies


def test_state_is_allowed_to_push_unknown_strategy() -> None:
    s = ShadowState()
    assert s.is_allowed_to_push("never-seen") is False


def test_state_round_trip(tmp_path: Path) -> None:
    s1 = ShadowState()
    r = s1.ensure("ruff-format")
    for _ in range(4):
        r.record(success=True)
    r.record(success=True)
    assert r.status == "active"
    p = tmp_path / "shadow.json"
    save(s1, p)
    s2 = load(p)
    assert s2.strategies["ruff-format"].status == "active"
    assert s2.is_allowed_to_push("ruff-format") is True


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    s = load(tmp_path / "absent.json")
    assert s.strategies == {}


def test_load_corrupt_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "shadow.json"
    p.write_text("not-json", encoding="utf-8")
    assert load(p).strategies == {}


def test_from_dict_rejects_negative_counters() -> None:
    s = ShadowState.from_dict(
        {
            "strategies": {
                "bad": {"status": "shadow", "observations": -1, "wins": 0, "losses": 0},
                "ok": {"status": "shadow", "observations": 0, "wins": 0, "losses": 0},
            }
        }
    )
    assert "bad" not in s.strategies
    assert "ok" in s.strategies


def test_from_dict_rejects_invalid_status() -> None:
    s = ShadowState.from_dict(
        {"strategies": {"bad": {"status": "not-a-status", "observations": 0, "wins": 0, "losses": 0}}}
    )
    assert "bad" not in s.strategies


@pytest.mark.parametrize("kind", ["shadow", "active", "retired", "review"])
def test_initial_status_argument(kind: str) -> None:
    s = ShadowState()
    rec = s.ensure("x", initial=kind)  # type: ignore[arg-type]
    assert rec.status == kind
