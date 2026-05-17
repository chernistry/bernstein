"""Hypothesis property tests for the criterion-aware retry budget.

Each test exercises an invariant that should hold across the entire
input space, not just hand-picked fixtures.
"""

from __future__ import annotations

from itertools import pairwise

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from bernstein.core.cost.retry_budget import (
    Criterion,
    DegradationKind,
    DuplicateCriterionError,
    RetryBudget,
    parse_retry_budget_spec,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_NAME_ALPHABET = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_",
    min_size=1,
    max_size=12,
).filter(lambda s: s[0] != "_" or len(s) > 1)


def _unique_names(min_size: int = 0, max_size: int = 6) -> st.SearchStrategy[list[str]]:
    return st.lists(_NAME_ALPHABET, min_size=min_size, max_size=max_size, unique=True)


_RETRIES = st.integers(min_value=0, max_value=20)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(retries=_RETRIES, names=_unique_names(max_size=6))
def test_consume_at_most_retries_times(retries: int, names: list[str]) -> None:
    """``consume()`` can authorise at most ``retries`` retries."""
    b = RetryBudget.from_names(retries=retries, names=names)
    authorised = sum(1 for _ in range(retries + 5) if b.consume().should_retry)
    assert authorised == retries


@given(retries=_RETRIES, names=_unique_names(max_size=6))
def test_attempts_left_plus_used_equals_retries_until_exhausted(retries: int, names: list[str]) -> None:
    """``attempts_left + attempts_used`` is invariant up to the cap."""
    b = RetryBudget.from_names(retries=retries, names=names)
    for _ in range(retries):
        assert b.attempts_left + b.attempts_used == retries
        b.consume()
    assert b.attempts_used == retries
    assert b.attempts_left == 0


@given(names=_unique_names(min_size=1, max_size=8))
def test_degradation_targets_match_policy_order(names: list[str]) -> None:
    """The first ``len(names)`` retries target criteria in policy order."""
    retries = len(names)
    b = RetryBudget.from_names(retries=retries, names=names)
    decisions = [b.consume() for _ in range(retries)]
    targeted = [d.degraded_criterion.name for d in decisions if d.degraded_criterion is not None]
    assert targeted == names


@given(retries=_RETRIES, names=_unique_names(max_size=6))
def test_levels_monotonically_non_increase(retries: int, names: list[str]) -> None:
    """A criterion's level never goes up across consume() calls."""
    b = RetryBudget.from_names(retries=retries, names=names)
    history: dict[str, list[int]] = {n: [b.criterion(n).level] for n in names}
    for _ in range(retries):
        b.consume()
        for n in names:
            history[n].append(b.criterion(n).level)
    for series in history.values():
        for prev, curr in pairwise(series):
            assert curr <= prev


@given(retries=_RETRIES, names=_unique_names(max_size=6))
def test_decision_either_retries_or_exhausts(retries: int, names: list[str]) -> None:
    """Every consume call yields a retry OR an exhausted decision."""
    b = RetryBudget.from_names(retries=retries, names=names)
    for _ in range(retries + 3):
        d = b.consume()
        if d.should_retry:
            assert d.degradation_kind in (
                DegradationKind.LOWERED,
                DegradationKind.NONE,
                DegradationKind.FLOORED,
            )
        else:
            assert d.degradation_kind is DegradationKind.NONE
            assert "exhausted" in d.reason.lower()


@given(names=_unique_names(max_size=6))
def test_duplicate_names_always_rejected(names: list[str]) -> None:
    """If a policy contains a duplicate name, construction fails."""
    assume(len(names) >= 1)
    dupes = [*names, names[0]]  # force a duplicate
    try:
        RetryBudget(
            retries=len(dupes),
            criterion_degradation=[Criterion(n) for n in dupes],
        )
    except DuplicateCriterionError:
        return
    raise AssertionError("Expected DuplicateCriterionError")


@given(retries=_RETRIES, names=_unique_names(max_size=4))
def test_peek_idempotent(retries: int, names: list[str]) -> None:
    """``peek()`` does not advance state; calling twice yields equal."""
    b = RetryBudget.from_names(retries=retries, names=names)
    p1 = b.peek()
    p2 = b.peek()
    assert p1 == p2
    assert b.attempts_used == 0


@given(retries=_RETRIES, names=_unique_names(max_size=6))
def test_to_dict_roundtrip_keys(retries: int, names: list[str]) -> None:
    """``to_dict()`` exposes a stable schema."""
    b = RetryBudget.from_names(retries=retries, names=names)
    d = b.to_dict()
    assert set(d.keys()) == {
        "retries",
        "attempts_used",
        "attempts_left",
        "criteria",
        "policy",
    }


@given(names=_unique_names(min_size=1, max_size=5))
def test_parse_spec_roundtrip(names: list[str]) -> None:
    """Spec-string -> budget -> assertions hold."""
    spec = f"{len(names)}, degrade: {'>'.join(names)}"
    b = parse_retry_budget_spec(spec)
    assert b.retries == len(names)
    assert [c.name for c in b.criterion_degradation] == names


@given(retries=_RETRIES)
def test_zero_or_more_retries_construction(retries: int) -> None:
    """Non-negative retries always construct successfully."""
    b = RetryBudget(retries=retries)
    assert b.attempts_left == retries
    assert b.is_exhausted == (retries == 0)


@given(retries=st.integers(min_value=-100, max_value=-1))
def test_negative_retries_rejected_property(retries: int) -> None:
    """Negative retries always fail construction."""
    try:
        RetryBudget(retries=retries)
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


@given(names=_unique_names(min_size=1, max_size=6))
def test_each_targeted_criterion_appears_in_snapshot(names: list[str]) -> None:
    """Every degraded criterion is present in the decision snapshot."""
    b = RetryBudget.from_names(retries=len(names), names=names)
    for _ in range(len(names)):
        d = b.consume()
        if d.degraded_criterion is None:
            continue
        snap_names = [c.name for c in d.criteria_snapshot]
        assert d.degraded_criterion.name in snap_names
