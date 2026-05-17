"""Unit tests for ``bernstein.core.autoheal.flake_detector``."""

from __future__ import annotations

import pytest

from bernstein.core.autoheal.flake_detector import TestHistoryPoint, classify


def _hist(pattern: str) -> list[TestHistoryPoint]:
    """Build a history from a string like ``"F.F.F"`` (F=fail, .=pass)."""
    out: list[TestHistoryPoint] = []
    for idx, ch in enumerate(pattern):
        if ch == "F":
            out.append(TestHistoryPoint(commit_index=idx, failed=True))
        else:
            out.append(TestHistoryPoint(commit_index=idx, failed=False))
    return out


def test_short_history_is_unknown() -> None:
    assert classify(_hist("F...")) == "unknown"
    assert classify(_hist("FFFF")) == "unknown"


def test_genuine_bug_is_real() -> None:
    # 5 commits: pass pass fail fail fail -> adjacent failures, real bug.
    assert classify(_hist("..FFF")) == "real"


def test_pass_fail_pass_pattern_is_flake() -> None:
    # 6 commits: F.F.F. -> three non-adjacent failures.
    assert classify(_hist("F.F.F.")) == "flake"


def test_too_few_failures_is_real() -> None:
    assert classify(_hist(".....")) == "real"
    assert classify(_hist(".F.F.")) == "real"  # only 2 failures


def test_long_window_with_clustered_failures_is_real() -> None:
    assert classify(_hist("...FFFFFF...")) == "real"


def test_long_window_with_scattered_failures_is_flake() -> None:
    assert classify(_hist("F....F....F.")) == "flake"


@pytest.mark.parametrize(
    "pattern",
    [
        "F.F.F.",
        ".F.F.F",
        "F..F..F",
        "FF.F.",
    ],
)
def test_known_flake_patterns(pattern: str) -> None:
    assert classify(_hist(pattern)) == "flake"


@pytest.mark.parametrize(
    "pattern",
    [
        ".FFF.",
        "FFF..",
        "..FFFF",
    ],
)
def test_known_real_patterns(pattern: str) -> None:
    assert classify(_hist(pattern)) == "real"


def test_classify_sorts_unordered_input() -> None:
    pts = [
        TestHistoryPoint(commit_index=2, failed=True),
        TestHistoryPoint(commit_index=0, failed=True),
        TestHistoryPoint(commit_index=1, failed=False),
        TestHistoryPoint(commit_index=4, failed=True),
        TestHistoryPoint(commit_index=3, failed=False),
    ]
    # Sorted: F.F.F -> non-adjacent triple.
    assert classify(pts) == "flake"
