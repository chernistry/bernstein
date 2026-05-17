"""Hypothesis property tests for the TOPSIS multi-criteria ranker.

These tests exercise the structural invariants of TOPSIS that any
correct implementation must hold regardless of input shape:

- permutation symmetry: shuffling the candidate order does not change
  the winner (only the tie-break) or the set of (key, closeness) pairs;
- scale invariance: uniformly multiplying every score by a positive
  constant preserves the ranking;
- monotonicity in a dominant benefit axis: raising a candidate's
  dominant-axis score can only improve its rank;
- determinism: the same inputs produce the same output on repeated
  calls;
- no NaN propagation: closeness coefficients are always finite and in
  ``[0, 1]``.

Smoke profile keeps each test under 30 s on a GitHub-hosted runner;
the nightly ``deep`` profile re-runs with 1 000 examples.
"""

from __future__ import annotations

import math
import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.orchestration.multi_criteria_rank import (
    Candidate,
    Criterion,
    CriterionProfile,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_FINITE = st.floats(
    min_value=-1e4,
    max_value=1e4,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
).filter(lambda v: v == 0.0 or abs(v) >= 1e-6)


def _candidates_strategy(min_size: int = 2, max_size: int = 6) -> st.SearchStrategy[list[Candidate]]:
    return st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=999),  # generate raw key id
            _FINITE,
            _FINITE,
        ),
        min_size=min_size,
        max_size=max_size,
        unique_by=lambda triple: triple[0],
    ).map(lambda triples: [Candidate(key=f"c{ix}", scores={"a": a, "b": b}) for ix, a, b in triples])


_PROFILE = CriterionProfile(
    criteria=(
        Criterion("a", direction="benefit", weight=1.0),
        Criterion("b", direction="benefit", weight=1.0),
    )
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(_candidates_strategy())
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_closeness_is_finite_and_bounded(candidates: list[Candidate]) -> None:
    for r in rank_candidates(candidates, _PROFILE):
        assert math.isfinite(r.closeness)
        assert 0.0 <= r.closeness <= 1.0


@given(_candidates_strategy())
def test_ranks_form_contiguous_one_to_n_sequence(candidates: list[Candidate]) -> None:
    result = rank_candidates(candidates, _PROFILE)
    assert [r.rank for r in result] == list(range(1, len(result) + 1))


@given(_candidates_strategy())
def test_no_nan_propagates_to_normalised_scores(candidates: list[Candidate]) -> None:
    for r in rank_candidates(candidates, _PROFILE):
        for v in r.normalised_scores.values():
            assert math.isfinite(v)


@given(_candidates_strategy(), st.integers(min_value=0, max_value=10_000))
def test_permutation_symmetry_of_winner(candidates: list[Candidate], seed: int) -> None:
    """Shuffling the input order must not change the (key, closeness) set."""
    rng = random.Random(seed)
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    base = {(r.key, round(r.closeness, 9)) for r in rank_candidates(candidates, _PROFILE)}
    shuf = {(r.key, round(r.closeness, 9)) for r in rank_candidates(shuffled, _PROFILE)}
    assert base == shuf


@given(_candidates_strategy(), st.integers(min_value=0, max_value=10_000))
def test_winner_key_is_stable_under_shuffle(candidates: list[Candidate], seed: int) -> None:
    rng = random.Random(seed)
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    w1 = rank_candidates(candidates, _PROFILE)[0].key
    w2 = rank_candidates(shuffled, _PROFILE)[0].key
    assert w1 == w2


@given(_candidates_strategy())
def test_determinism_repeated_calls(candidates: list[Candidate]) -> None:
    r1 = rank_candidates(candidates, _PROFILE)
    r2 = rank_candidates(candidates, _PROFILE)
    r3 = rank_candidates(candidates, _PROFILE)
    assert [c.key for c in r1] == [c.key for c in r2] == [c.key for c in r3]
    for a, b in zip(r1, r2, strict=True):
        assert a.closeness == b.closeness


@given(
    _candidates_strategy(),
    st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
)
def test_uniform_positive_scale_preserves_ranking(candidates: list[Candidate], scale: float) -> None:
    scaled = [Candidate(c.key, {k: v * scale for k, v in c.scores.items()}) for c in candidates]
    base = rank_candidates(candidates, _PROFILE)
    sc = rank_candidates(scaled, _PROFILE)
    # Closeness coefficients are invariant under positive scale; compare
    # the per-key closeness rather than the list-order which can swap on
    # float-tie boundaries.
    by_key_base = {r.key: r.closeness for r in base}
    by_key_sc = {r.key: r.closeness for r in sc}
    for key, c_base in by_key_base.items():
        assert by_key_sc[key] == pytest.approx(c_base, abs=1e-9)


@given(_candidates_strategy())
def test_winner_dominates_or_ties_on_closeness(candidates: list[Candidate]) -> None:
    """The first-place candidate's closeness is >= every other's."""
    result = rank_candidates(candidates, _PROFILE)
    top = result[0].closeness
    for r in result[1:]:
        assert top >= r.closeness


@given(_candidates_strategy())
def test_output_length_equals_input_length(candidates: list[Candidate]) -> None:
    assert len(rank_candidates(candidates, _PROFILE)) == len(candidates)


@given(_candidates_strategy())
def test_output_keys_are_a_permutation_of_input_keys(
    candidates: list[Candidate],
) -> None:
    in_keys = sorted(c.key for c in candidates)
    out_keys = sorted(r.key for r in rank_candidates(candidates, _PROFILE))
    assert in_keys == out_keys


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=999),
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        ),
        min_size=2,
        max_size=5,
        unique_by=lambda t: t[0],
    )
)
def test_monotonicity_in_dominant_axis(triples: list[tuple[int, float]]) -> None:
    """Raising the dominant-axis score of the current winner cannot
    demote it.

    This is the standard "Pareto-improving the winner keeps it winning"
    property under a single-axis profile.
    """
    cands = [Candidate(f"c{ix}", {"x": v}) for ix, v in triples]
    profile = CriterionProfile(criteria=(Criterion("x"),))
    winner_key = rank_candidates(cands, profile)[0].key
    boosted = [Candidate(c.key, {"x": c.scores["x"] + 1000.0 if c.key == winner_key else c.scores["x"]}) for c in cands]
    new_winner = rank_candidates(boosted, profile)[0].key
    assert new_winner == winner_key


@given(_candidates_strategy())
def test_normalised_scores_have_every_profile_axis(
    candidates: list[Candidate],
) -> None:
    for r in rank_candidates(candidates, _PROFILE):
        assert set(r.normalised_scores.keys()) == set(_PROFILE.names)


@given(
    _candidates_strategy(),
    st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
)
def test_uniform_weight_scaling_preserves_ranking(candidates: list[Candidate], scale: float) -> None:
    p1 = CriterionProfile(criteria=(Criterion("a", weight=1.0), Criterion("b", weight=1.0)))
    p2 = CriterionProfile(criteria=(Criterion("a", weight=scale), Criterion("b", weight=scale)))
    keys1 = [r.key for r in rank_candidates(candidates, p1)]
    keys2 = [r.key for r in rank_candidates(candidates, p2)]
    assert keys1 == keys2


@given(_candidates_strategy())
def test_closeness_is_non_increasing_across_rank(
    candidates: list[Candidate],
) -> None:
    """The closeness sequence is monotonically non-increasing by rank."""
    from itertools import pairwise

    result = rank_candidates(candidates, _PROFILE)
    closenesses = [r.closeness for r in result]
    for a, b in pairwise(closenesses):
        assert a >= b


@given(_candidates_strategy())
def test_zero_weight_axis_does_not_change_winner(
    candidates: list[Candidate],
) -> None:
    """Adding an axis whose weight is zero must not change the winner."""
    base = CriterionProfile(criteria=(Criterion("a"), Criterion("b")))
    augmented = CriterionProfile(criteria=(Criterion("a"), Criterion("b"), Criterion("ghost", weight=0.0)))
    # Augment each candidate with a random ghost score.
    cands_ext = [Candidate(c.key, {**c.scores, "ghost": float(i)}) for i, c in enumerate(candidates)]
    w_base = rank_candidates(candidates, base)[0].key
    w_ext = rank_candidates(cands_ext, augmented)[0].key
    assert w_base == w_ext
