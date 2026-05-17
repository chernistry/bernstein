"""Unit tests for ``bernstein.core.autoheal.provenance``."""

from __future__ import annotations

from bernstein.core.autoheal.provenance import (
    HALF_LIFE_SECONDS,
    BlameInfo,
    score_line,
)


def _oracle_const(epoch: float):  # type: ignore[no-untyped-def]
    def blame(path: str, line: int) -> BlameInfo | None:
        return BlameInfo(path=path, line=line, author_time_epoch=epoch)

    return blame


def _oracle_none():  # type: ignore[no-untyped-def]
    def blame(path: str, line: int) -> BlameInfo | None:
        return None

    return blame


def test_fresh_line_scores_one() -> None:
    now = 1700000000.0
    res = score_line("foo.py", 10, _oracle_const(now - 60), now=now)
    assert res.score == 1.0


def test_one_day_old_at_boundary_scores_one() -> None:
    now = 1700000000.0
    res = score_line("foo.py", 10, _oracle_const(now - HALF_LIFE_SECONDS), now=now)
    assert res.score == 1.0


def test_two_day_old_decays() -> None:
    now = 1700000000.0
    # Just past the half-life cliff -> the next decay window applies.
    res = score_line("foo.py", 10, _oracle_const(now - HALF_LIFE_SECONDS * 2), now=now)
    assert 0.0 < res.score < 1.0


def test_very_old_line_scores_low() -> None:
    now = 1700000000.0
    res = score_line("foo.py", 10, _oracle_const(now - HALF_LIFE_SECONDS * 30), now=now)
    assert res.score < 0.01


def test_blame_oracle_returning_none_scores_zero() -> None:
    res = score_line("foo.py", 10, _oracle_none(), now=1700000000.0)
    assert res.score == 0.0


def test_score_is_within_unit_interval() -> None:
    now = 1700000000.0
    for delta in [0, 60, 3600, 86400, 86400 * 2, 86400 * 7, 86400 * 365]:
        res = score_line("foo.py", 10, _oracle_const(now - delta), now=now)
        assert 0.0 <= res.score <= 1.0


def test_age_in_seconds_is_correct() -> None:
    now = 1700000000.0
    res = score_line("foo.py", 10, _oracle_const(now - 3600), now=now)
    assert res.age_seconds == 3600.0


def test_score_is_monotonically_decreasing_in_age() -> None:
    now = 1700000000.0
    last = 1.0
    for delta in [0, HALF_LIFE_SECONDS, HALF_LIFE_SECONDS * 2, HALF_LIFE_SECONDS * 10]:
        res = score_line("p", 1, _oracle_const(now - delta), now=now)
        assert res.score <= last + 1e-9
        last = res.score
