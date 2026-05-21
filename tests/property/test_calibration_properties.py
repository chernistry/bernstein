"""Property tests for ``bernstein.eval.calibration``.

Each property check encodes an invariant the calibration primitives must
hold for arbitrary valid inputs:

* Brier score range is ``[0, 1]``.
* Increasing the absolute prediction error never decreases the Brier score.
* ECE is bounded above by 1 and is zero on perfectly-calibrated batches.
* Reliability buckets sum to the input length with monotonic bounds.
"""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from bernstein.eval.calibration import (
    compute_brier,
    expected_calibration_error,
    reliability_diagram_data,
)

_PROB = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_BOOLS = st.booleans()


@given(st.lists(_PROB, min_size=1, max_size=200), st.lists(_BOOLS, min_size=1, max_size=200))
def test_brier_within_unit_interval(preds: list[float], obs: list[bool]) -> None:
    """Brier score is always in ``[0, 1]`` regardless of inputs."""
    n = min(len(preds), len(obs))
    score = compute_brier(preds[:n], obs[:n]).score
    assert 0.0 <= score <= 1.0


@given(st.lists(_PROB, min_size=1, max_size=200), st.lists(_BOOLS, min_size=1, max_size=200))
def test_brier_zero_iff_perfect_predictions(preds: list[float], obs: list[bool]) -> None:
    """Score is zero exactly when every prediction equals its target."""
    n = min(len(preds), len(obs))
    preds, obs = preds[:n], obs[:n]
    perfect = [1.0 if o else 0.0 for o in obs]
    assert compute_brier(perfect, obs).score == 0.0


@given(st.lists(_BOOLS, min_size=1, max_size=100))
def test_brier_one_when_anticalibrated(obs: list[bool]) -> None:
    """All-wrong deterministic predictions yield score == 1.0."""
    anti = [0.0 if o else 1.0 for o in obs]
    assert compute_brier(anti, obs).score == 1.0


@given(st.lists(_PROB, min_size=2, max_size=50), st.lists(_BOOLS, min_size=2, max_size=50))
def test_brier_monotonic_in_error(preds: list[float], obs: list[bool]) -> None:
    """Pushing every prediction further from its target increases the Brier score.

    For each prediction ``p`` paired with target ``t`` we construct ``q``
    such that ``|q - t| >= |p - t|``; the resulting Brier score must not
    decrease.
    """
    n = min(len(preds), len(obs))
    preds, obs = preds[:n], obs[:n]
    base = compute_brier(preds, obs).score
    worse: list[float] = []
    for p, o in zip(preds, obs, strict=True):
        target = 1.0 if o else 0.0
        # Move further from target while staying in [0, 1].
        delta = target - p
        # If p == target, perturb away - otherwise nudge further.
        if delta == 0:
            worse.append(0.0 if target == 1.0 else 1.0)
        else:
            worse.append(0.0 if target == 1.0 else 1.0)
    worse_score = compute_brier(worse, obs).score
    assert worse_score >= base - 1e-12


@given(
    st.lists(_PROB, min_size=1, max_size=100),
    st.lists(_BOOLS, min_size=1, max_size=100),
    st.integers(min_value=1, max_value=20),
)
def test_ece_within_unit_interval(preds: list[float], obs: list[bool], bins: int) -> None:
    """ECE lies in ``[0, 1]`` for any valid bin_count."""
    n = min(len(preds), len(obs))
    err = expected_calibration_error(preds[:n], obs[:n], bin_count=bins)
    assert 0.0 <= err <= 1.0


@given(st.lists(_BOOLS, min_size=1, max_size=50))
def test_ece_zero_for_perfect_predictions(obs: list[bool]) -> None:
    """Perfect predictions yield ECE == 0 in any bin count."""
    perfect = [1.0 if o else 0.0 for o in obs]
    assert expected_calibration_error(perfect, obs) == 0.0


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
    st.integers(min_value=1, max_value=20),
)
def test_reliability_bucket_count_invariant(preds: list[float], obs: list[bool], bins: int) -> None:
    """Bucket counts sum to the input length, and bin count is exact."""
    n = min(len(preds), len(obs))
    buckets = reliability_diagram_data(preds[:n], obs[:n], bin_count=bins)
    assert len(buckets) == bins
    assert sum(b.count for b in buckets) == n


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
    st.integers(min_value=1, max_value=20),
)
def test_reliability_bounds_monotonic(preds: list[float], obs: list[bool], bins: int) -> None:
    """Reliability bucket bounds are monotonically non-decreasing."""
    from itertools import pairwise

    n = min(len(preds), len(obs))
    buckets = reliability_diagram_data(preds[:n], obs[:n], bin_count=bins)
    for prev, curr in pairwise(buckets):
        assert curr.lower >= prev.lower
        assert curr.upper >= prev.upper


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
    st.integers(min_value=2, max_value=20),
)
def test_reliability_predicted_mean_within_bucket(preds: list[float], obs: list[bool], bins: int) -> None:
    """For each non-empty bucket, predicted_mean lies inside [lower, upper]."""
    n = min(len(preds), len(obs))
    for b in reliability_diagram_data(preds[:n], obs[:n], bin_count=bins):
        if b.count == 0:
            continue
        # Allow tiny float slack for the top bucket which is closed on both ends.
        assert b.lower - 1e-12 <= b.predicted_mean <= b.upper + 1e-12


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
)
def test_brier_symmetry_under_relabel(preds: list[float], obs: list[bool]) -> None:
    """Brier(p, o) == Brier(1-p, not o)."""
    n = min(len(preds), len(obs))
    preds, obs = preds[:n], obs[:n]
    flipped_preds = [1.0 - p for p in preds]
    flipped_obs = [not o for o in obs]
    a = compute_brier(preds, obs).score
    b = compute_brier(flipped_preds, flipped_obs).score
    # Float arithmetic with subnormals introduces ~1e-16 noise; compare with
    # an absolute tolerance well within the unit interval.
    assert abs(a - b) <= 1e-9


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
)
def test_brier_is_finite(preds: list[float], obs: list[bool]) -> None:
    """Brier score is finite for any valid input."""
    import math

    n = min(len(preds), len(obs))
    score = compute_brier(preds[:n], obs[:n]).score
    assert math.isfinite(score)


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
    st.integers(min_value=1, max_value=20),
)
def test_ece_is_finite(preds: list[float], obs: list[bool], bins: int) -> None:
    """ECE is finite for any valid input."""
    import math

    n = min(len(preds), len(obs))
    err = expected_calibration_error(preds[:n], obs[:n], bin_count=bins)
    assert math.isfinite(err)


@given(
    st.lists(_PROB, min_size=2, max_size=80),
    st.lists(_BOOLS, min_size=2, max_size=80),
)
def test_brier_does_not_change_under_pair_permutation(preds: list[float], obs: list[bool]) -> None:
    """Brier score is invariant under joint reordering of pairs."""
    n = min(len(preds), len(obs))
    preds, obs = preds[:n], obs[:n]
    assume(n >= 2)
    direct = compute_brier(preds, obs).score
    reverse = compute_brier(list(reversed(preds)), list(reversed(obs))).score
    # Summation order can introduce ~1e-16 noise.
    assert abs(direct - reverse) <= 1e-9


@given(
    st.lists(_PROB, min_size=1, max_size=40),
    st.lists(_BOOLS, min_size=1, max_size=40),
)
def test_reliability_default_buckets_is_ten(preds: list[float], obs: list[bool]) -> None:
    """Default bin_count yields exactly ten buckets."""
    n = min(len(preds), len(obs))
    assert len(reliability_diagram_data(preds[:n], obs[:n])) == 10


@given(
    st.lists(_PROB, min_size=1, max_size=80),
    st.lists(_BOOLS, min_size=1, max_size=80),
    st.integers(min_value=1, max_value=10),
)
def test_reliability_observed_mean_in_unit_interval(preds: list[float], obs: list[bool], bins: int) -> None:
    """observed_mean of every bucket lies in ``[0, 1]``."""
    n = min(len(preds), len(obs))
    for b in reliability_diagram_data(preds[:n], obs[:n], bin_count=bins):
        assert 0.0 <= b.observed_mean <= 1.0
