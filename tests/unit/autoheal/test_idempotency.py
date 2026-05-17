"""Unit tests for ``bernstein.core.autoheal.idempotency``."""

from __future__ import annotations

import time

import pytest

from bernstein.core.autoheal.audit_log import HealRecord
from bernstein.core.autoheal.idempotency import (
    DEDUPE_WINDOW_SECONDS,
    check,
    patch_sha_for,
)


def _record(*, patch_sha: str, outcome: str, ts: float | None = None) -> HealRecord:
    return HealRecord(
        ts=ts if ts is not None else time.time(),
        run_id="r",
        head_sha="h",
        strategy="x",
        cls="safe",
        confidence=0.5,
        outcome=outcome,  # type: ignore[arg-type]
        patch_sha=patch_sha,
    )


def test_patch_sha_is_stable_for_same_input() -> None:
    a = patch_sha_for(b"some diff bytes")
    b = patch_sha_for(b"some diff bytes")
    assert a == b


def test_patch_sha_differs_for_different_input() -> None:
    a = patch_sha_for(b"one")
    b = patch_sha_for(b"two")
    assert a != b


def test_patch_sha_length_is_16_hex() -> None:
    s = patch_sha_for(b"x")
    assert len(s) == 16
    int(s, 16)  # parseable as hex


def test_empty_candidate_sha_is_rejected() -> None:
    d = check("", [], now=time.time())
    assert d.allowed is False
    assert d.reason == "empty_patch_sha"


def test_no_history_allows() -> None:
    d = check("abc", [], now=time.time())
    assert d.allowed is True


def test_recent_failure_blocks_retry() -> None:
    now = 1700000000.0
    rec = _record(patch_sha="abc", outcome="failed_validation", ts=now - 60)
    d = check("abc", [rec], now=now)
    assert d.allowed is False
    assert "recent_failure_within_" in d.reason


def test_old_failure_does_not_block() -> None:
    now = 1700000000.0
    rec = _record(
        patch_sha="abc",
        outcome="failed_validation",
        ts=now - DEDUPE_WINDOW_SECONDS - 1,
    )
    d = check("abc", [rec], now=now)
    assert d.allowed is True


def test_prior_applied_record_does_not_block() -> None:
    now = 1700000000.0
    rec = _record(patch_sha="abc", outcome="applied", ts=now - 60)
    d = check("abc", [rec], now=now)
    assert d.allowed is True


def test_different_patch_sha_does_not_collide() -> None:
    now = 1700000000.0
    rec = _record(patch_sha="zzz", outcome="failed_validation", ts=now - 60)
    d = check("abc", [rec], now=now)
    assert d.allowed is True


@pytest.mark.parametrize(
    "outcome",
    ["failed_validation", "failed_push", "escalated", "skipped_budget"],
)
def test_any_failure_outcome_within_window_blocks(outcome: str) -> None:
    now = 1700000000.0
    rec = _record(patch_sha="abc", outcome=outcome, ts=now - 60)
    d = check("abc", [rec], now=now)
    assert d.allowed is False
