"""Unit tests for :mod:`bernstein.core.cost.retry_budget`.

Covers budget exhaustion, criterion degradation ordering, illegal
degradation rejection, zero-retry edge cases, and the CLI spec parser.

A sibling module ``tests/unit/test_retry_budget.py`` already exists for
the *task-level* retry budget under ``bernstein.core.cost.planned`` -
this file is the criterion-aware (issue #1352) suite.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from bernstein.core.cost.retry_budget import (
    Criterion,
    CriterionExhaustedError,
    DegradationKind,
    DuplicateCriterionError,
    RetryBudget,
    RetryBudgetError,
    RetryDecision,
    UnknownCriterionError,
    parse_retry_budget_spec,
)

# ---------------------------------------------------------------------------
# Criterion
# ---------------------------------------------------------------------------


class TestCriterion:
    def test_default_levels(self) -> None:
        c = Criterion(name="coverage")
        assert c.name == "coverage"
        assert c.level == 3
        assert c.min_level == 0
        assert c.max_level == 3
        assert not c.is_at_floor

    def test_at_floor_flag(self) -> None:
        c = Criterion(name="tests", level=0, min_level=0, max_level=3)
        assert c.is_at_floor

    def test_degrade_decrements_level(self) -> None:
        c = Criterion(name="style", level=2)
        d = c.degraded()
        assert d.level == 1
        assert d.name == "style"
        # original is unchanged (frozen dataclass).
        assert c.level == 2

    def test_degrade_at_floor_raises(self) -> None:
        c = Criterion(name="x", level=0, min_level=0)
        with pytest.raises(CriterionExhaustedError):
            c.degraded()

    def test_degrade_walks_to_floor(self) -> None:
        c = Criterion(name="x", level=3)
        c = c.degraded()
        c = c.degraded()
        c = c.degraded()
        assert c.level == 0
        assert c.is_at_floor
        with pytest.raises(CriterionExhaustedError):
            c.degraded()

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            Criterion(name="")

    def test_min_above_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_level"):
            Criterion(name="bad", level=1, min_level=5, max_level=1)

    def test_level_above_max_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            Criterion(name="bad", level=5, max_level=3)

    def test_level_below_min_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside"):
            Criterion(name="bad", level=-1, min_level=0)

    def test_reset_restores_max_level(self) -> None:
        c = Criterion(name="x", level=0, max_level=3)
        r = c.reset()
        assert r.level == 3

    def test_criterion_is_hashable(self) -> None:
        c = Criterion(name="x", level=2)
        assert hash(c) == hash(Criterion(name="x", level=2))


# ---------------------------------------------------------------------------
# RetryBudget construction
# ---------------------------------------------------------------------------


class TestRetryBudgetConstruction:
    def test_basic_construction(self) -> None:
        b = RetryBudget(retries=3, criterion_degradation=[Criterion("coverage")])
        assert b.retries == 3
        assert b.attempts_used == 0
        assert b.attempts_left == 3
        assert not b.is_exhausted

    def test_zero_retries(self) -> None:
        b = RetryBudget(retries=0)
        assert b.is_exhausted
        assert b.attempts_left == 0

    def test_negative_retries_rejected(self) -> None:
        with pytest.raises(ValueError, match="retries must be >= 0"):
            RetryBudget(retries=-1)

    def test_duplicate_criterion_rejected(self) -> None:
        with pytest.raises(DuplicateCriterionError):
            RetryBudget(
                retries=2,
                criterion_degradation=[
                    Criterion("coverage"),
                    Criterion("coverage"),
                ],
            )

    def test_from_names_helper(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["a", "b"])
        assert b.retries == 2
        assert [c.name for c in b.criteria] == ["a", "b"]
        assert all(c.level == 3 for c in b.criteria)

    def test_from_names_with_custom_bounds(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["x"], max_level=5, min_level=1)
        assert b.criteria[0].level == 5
        assert b.criteria[0].max_level == 5
        assert b.criteria[0].min_level == 1

    def test_empty_policy_allowed(self) -> None:
        b = RetryBudget(retries=2)
        assert b.criteria == ()
        assert b.attempts_left == 2

    def test_construct_with_pre_degraded_criterion(self) -> None:
        b = RetryBudget(
            retries=1,
            criterion_degradation=[Criterion("c", level=1, min_level=0)],
        )
        assert b.criterion("c").level == 1


# ---------------------------------------------------------------------------
# RetryBudget.consume / peek
# ---------------------------------------------------------------------------


class TestRetryBudgetConsume:
    def test_consume_returns_decision(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["coverage", "tests"])
        d = b.consume()
        assert isinstance(d, RetryDecision)
        assert d.should_retry
        assert d.attempt_index == 0

    def test_consume_decrements_attempts_left(self) -> None:
        b = RetryBudget.from_names(retries=3, names=["a"])
        assert b.attempts_left == 3
        b.consume()
        assert b.attempts_left == 2
        b.consume()
        assert b.attempts_left == 1

    def test_consume_exhausted_returns_no_retry(self) -> None:
        b = RetryBudget(retries=0)
        d = b.consume()
        assert not d.should_retry
        assert d.degradation_kind is DegradationKind.NONE
        assert "exhausted" in d.reason.lower()

    def test_consume_after_exhaustion_is_idempotent(self) -> None:
        b = RetryBudget(retries=1)
        b.consume()
        d2 = b.consume()
        assert not d2.should_retry
        assert b.attempts_used == 1

    def test_degradation_first_retry_targets_first_criterion(self) -> None:
        b = RetryBudget.from_names(retries=3, names=["coverage", "tests", "style"])
        d = b.consume()
        assert d.degraded_criterion is not None
        assert d.degraded_criterion.name == "coverage"
        assert d.degraded_criterion.level == 2

    def test_degradation_second_retry_targets_second_criterion(self) -> None:
        b = RetryBudget.from_names(retries=3, names=["coverage", "tests", "style"])
        b.consume()
        d = b.consume()
        assert d.degraded_criterion is not None
        assert d.degraded_criterion.name == "tests"
        assert d.degraded_criterion.level == 2

    def test_degradation_third_retry_targets_third_criterion(self) -> None:
        b = RetryBudget.from_names(retries=3, names=["coverage", "tests", "style"])
        b.consume()
        b.consume()
        d = b.consume()
        assert d.degraded_criterion is not None
        assert d.degraded_criterion.name == "style"

    def test_more_retries_than_criteria_no_degradation(self) -> None:
        b = RetryBudget.from_names(retries=5, names=["coverage"])
        b.consume()  # coverage 3->2
        b.consume()  # no policy entry at idx=1
        d = b.consume()
        assert d.should_retry
        assert d.degraded_criterion is None
        assert d.degradation_kind is DegradationKind.NONE

    def test_peek_does_not_mutate_state(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["coverage"])
        p1 = b.peek()
        p2 = b.peek()
        assert b.attempts_used == 0
        assert p1.attempt_index == p2.attempt_index == 0
        assert b.criterion("coverage").level == 3

    def test_peek_then_consume_consistent(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["coverage"])
        p = b.peek()
        d = b.consume()
        assert p.attempt_index == d.attempt_index
        assert p.degraded_criterion == d.degraded_criterion

    def test_criterion_already_at_floor_yields_floored_kind(self) -> None:
        b = RetryBudget(
            retries=2,
            criterion_degradation=[
                Criterion("coverage", level=0, min_level=0, max_level=3),
            ],
        )
        d = b.consume()
        assert d.should_retry
        assert d.degradation_kind is DegradationKind.FLOORED
        assert d.degraded_criterion is not None
        assert d.degraded_criterion.level == 0

    def test_consume_n_times_yields_n_attempts_used(self) -> None:
        b = RetryBudget.from_names(retries=4, names=["a", "b", "c", "d"])
        for _ in range(4):
            b.consume()
        assert b.attempts_used == 4
        assert b.is_exhausted

    def test_attempts_left_never_negative(self) -> None:
        b = RetryBudget(retries=1)
        b.consume()
        b.consume()
        b.consume()
        assert b.attempts_left == 0


# ---------------------------------------------------------------------------
# RetryDecision
# ---------------------------------------------------------------------------


class TestRetryDecision:
    def test_decision_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        b = RetryBudget.from_names(retries=1, names=["c"])
        d = b.consume()
        with pytest.raises(FrozenInstanceError):
            d.attempt_index = 99  # type: ignore[misc]

    def test_decision_criterion_lookup(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["coverage"])
        d = b.consume()
        c = d.criterion("coverage")
        assert c.name == "coverage"

    def test_decision_criterion_unknown_raises(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["coverage"])
        d = b.consume()
        with pytest.raises(UnknownCriterionError):
            d.criterion("nonexistent")

    def test_decision_reason_mentions_retry_number(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["coverage"])
        d = b.consume()
        assert "retry #1" in d.reason

    def test_decision_snapshot_reflects_post_degradation(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["coverage"])
        d = b.consume()
        snap = {c.name: c.level for c in d.criteria_snapshot}
        assert snap["coverage"] == 2

    def test_decision_after_exhaustion_carries_full_snapshot(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["coverage"])
        b.consume()
        d = b.consume()
        # snapshot still contains coverage
        assert any(c.name == "coverage" for c in d.criteria_snapshot)


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_criterion_lookup_by_name(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["coverage", "tests"])
        assert b.criterion("coverage").name == "coverage"
        assert b.criterion("tests").name == "tests"

    def test_criterion_unknown_name_raises(self) -> None:
        b = RetryBudget.from_names(retries=1, names=["coverage"])
        with pytest.raises(UnknownCriterionError):
            b.criterion("nope")

    def test_criteria_property_preserves_order(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["z", "a", "m"])
        names = [c.name for c in b.criteria]
        assert names == ["z", "a", "m"]

    def test_to_dict_serialises_state(self) -> None:
        b = RetryBudget.from_names(retries=2, names=["coverage"])
        b.consume()
        d = b.to_dict()
        assert d["retries"] == 2
        assert d["attempts_used"] == 1
        assert d["attempts_left"] == 1
        assert d["policy"] == ["coverage"]
        assert isinstance(d["criteria"], list)


# ---------------------------------------------------------------------------
# CLI spec parser
# ---------------------------------------------------------------------------


class TestParseSpec:
    def test_basic_form(self) -> None:
        b = parse_retry_budget_spec("3 retries, degrade: coverage>tests>style")
        assert b.retries == 3
        assert [c.name for c in b.criterion_degradation] == [
            "coverage",
            "tests",
            "style",
        ]

    def test_no_degrade_keyword(self) -> None:
        b = parse_retry_budget_spec("2, coverage>tests")
        assert b.retries == 2
        assert [c.name for c in b.criterion_degradation] == ["coverage", "tests"]

    def test_single_criterion(self) -> None:
        b = parse_retry_budget_spec("1 retry, degrade: coverage")
        assert b.retries == 1
        assert len(b.criterion_degradation) == 1

    def test_zero_retries(self) -> None:
        b = parse_retry_budget_spec("0")
        assert b.retries == 0
        assert b.is_exhausted

    def test_extra_whitespace_tolerated(self) -> None:
        b = parse_retry_budget_spec("  3   retries  ,   degrade :  coverage  >  tests  >  style  ")
        assert b.retries == 3
        assert [c.name for c in b.criterion_degradation] == [
            "coverage",
            "tests",
            "style",
        ]

    def test_empty_spec_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_retry_budget_spec("")

    def test_whitespace_spec_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_retry_budget_spec("   \t  ")

    def test_garbage_spec_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_retry_budget_spec("not a number")

    def test_empty_criterion_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty criterion"):
            parse_retry_budget_spec("3, coverage>>tests")

    def test_duplicate_criterion_rejected(self) -> None:
        with pytest.raises(DuplicateCriterionError):
            parse_retry_budget_spec("3, coverage>coverage")

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid criterion name"):
            parse_retry_budget_spec("3, 9bad")

    def test_known_criteria_whitelist_applies_bounds(self) -> None:
        known = {"coverage": Criterion("coverage", max_level=5, level=5)}
        b = parse_retry_budget_spec("1, coverage", known_criteria=known)
        assert b.criterion("coverage").max_level == 5

    def test_known_criteria_rejects_unknown(self) -> None:
        with pytest.raises(UnknownCriterionError):
            parse_retry_budget_spec(
                "1, coverage>tests",
                known_criteria={"coverage": Criterion("coverage")},
            )

    def test_attempts_synonym_works(self) -> None:
        b = parse_retry_budget_spec("4 attempts")
        assert b.retries == 4

    def test_semicolon_separator(self) -> None:
        b = parse_retry_budget_spec("2; coverage>tests")
        assert b.retries == 2
        assert len(b.criterion_degradation) == 2

    def test_rejects_adversarial_policy_without_regex_backtracking(self) -> None:
        code = """
from bernstein.core.cost.retry_budget import parse_retry_budget_spec
try:
    parse_retry_budget_spec("1, " + ("a>" * 50) + ",")
except ValueError:
    pass
"""
        subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            timeout=5,
        )


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_all_errors_subclass_base(self) -> None:
        for exc in (
            CriterionExhaustedError,
            DuplicateCriterionError,
            UnknownCriterionError,
        ):
            assert issubclass(exc, RetryBudgetError)

    def test_unknown_criterion_carries_known_set(self) -> None:
        try:
            RetryBudget.from_names(retries=1, names=["a"]).criterion("missing")
        except UnknownCriterionError as exc:
            assert exc.name == "missing"
            assert "a" in exc.known
        else:  # pragma: no cover - defensive
            pytest.fail("expected UnknownCriterionError")

    def test_duplicate_criterion_carries_name(self) -> None:
        try:
            RetryBudget(
                retries=1,
                criterion_degradation=[Criterion("x"), Criterion("x")],
            )
        except DuplicateCriterionError as exc:
            assert exc.name == "x"
        else:  # pragma: no cover - defensive
            pytest.fail("expected DuplicateCriterionError")

    def test_criterion_exhausted_carries_criterion(self) -> None:
        c = Criterion("x", level=0, min_level=0)
        try:
            c.degraded()
        except CriterionExhaustedError as exc:
            assert exc.criterion.name == "x"
        else:  # pragma: no cover - defensive
            pytest.fail("expected CriterionExhaustedError")


# ---------------------------------------------------------------------------
# End-to-end scenarios
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_full_degradation_sequence(self) -> None:
        b = RetryBudget.from_names(retries=3, names=["coverage", "tests", "style"])
        decisions = [b.consume() for _ in range(4)]
        retry_names = [d.degraded_criterion.name for d in decisions[:3] if d.degraded_criterion is not None]
        assert retry_names == ["coverage", "tests", "style"]
        assert not decisions[3].should_retry

    def test_zero_retry_budget_routes_to_dlq(self) -> None:
        b = RetryBudget(retries=0)
        d = b.consume()
        assert d.should_retry is False

    def test_floor_walk_respects_min_level(self) -> None:
        b = RetryBudget(
            retries=2,
            criterion_degradation=[
                Criterion("coverage", level=3, min_level=2, max_level=3),
            ],
        )
        d = b.consume()
        assert d.degraded_criterion is not None
        assert d.degraded_criterion.level == 2
        # No further consumption degrades - second consume has no
        # criterion at index 1.
        d2 = b.consume()
        assert d2.should_retry
        assert d2.degraded_criterion is None
