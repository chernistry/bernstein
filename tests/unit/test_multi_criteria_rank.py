"""Unit tests for :mod:`bernstein.core.orchestration.multi_criteria_rank`.

Covers degenerate input handling (0 / 1 candidate), score validation
(missing axis, NaN, infinity, booleans), profile builders, weight
edge cases (zero / negative / non-normalised sums), direction handling
(benefit vs cost), and deterministic tie-breaks.  These tests are
intentionally dense so future refactors of the TOPSIS internals cannot
silently drift the ranking behaviour.
"""

from __future__ import annotations

import math

import pytest

from bernstein.core.orchestration.multi_criteria_rank import (
    Candidate,
    Criterion,
    CriterionProfile,
    RankedCandidate,
    TopsisError,
    build_criterion_profile,
    parse_criteria_csv,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cand(key: str, **scores: float) -> Candidate:
    return Candidate(key=key, scores=dict(scores))


# ---------------------------------------------------------------------------
# Criterion validation
# ---------------------------------------------------------------------------


def test_criterion_default_direction_is_benefit() -> None:
    c = Criterion(name="correctness")
    assert c.direction == "benefit"
    assert c.weight == pytest.approx(1.0)


def test_criterion_empty_name_rejected() -> None:
    with pytest.raises(TopsisError, match="non-empty"):
        Criterion(name="")


def test_criterion_unknown_direction_rejected() -> None:
    with pytest.raises(TopsisError, match="direction"):
        Criterion(name="x", direction="maximise")


def test_criterion_negative_weight_rejected() -> None:
    with pytest.raises(TopsisError, match="non-negative"):
        Criterion(name="x", weight=-0.5)


def test_criterion_nan_weight_rejected() -> None:
    with pytest.raises(TopsisError, match="finite"):
        Criterion(name="x", weight=float("nan"))


def test_criterion_infinite_weight_rejected() -> None:
    with pytest.raises(TopsisError, match="finite"):
        Criterion(name="x", weight=float("inf"))


def test_criterion_zero_weight_accepted() -> None:
    c = Criterion(name="x", weight=0.0)
    assert c.weight == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# CriterionProfile validation
# ---------------------------------------------------------------------------


def test_profile_requires_at_least_one_criterion() -> None:
    with pytest.raises(TopsisError, match="at least one"):
        CriterionProfile(criteria=())


def test_profile_rejects_duplicate_names() -> None:
    with pytest.raises(TopsisError, match="Duplicate"):
        CriterionProfile(criteria=(Criterion("a"), Criterion("a")))


def test_profile_names_property_preserves_order() -> None:
    p = CriterionProfile(criteria=(Criterion("b"), Criterion("a"), Criterion("c")))
    assert p.names == ("b", "a", "c")


# ---------------------------------------------------------------------------
# build_criterion_profile helper
# ---------------------------------------------------------------------------


def test_build_profile_identity_weights_when_unspecified() -> None:
    p = build_criterion_profile(["a", "b", "c"])
    assert all(c.weight == pytest.approx(1.0) for c in p.criteria)


def test_build_profile_marks_cost_axes_by_default() -> None:
    p = build_criterion_profile(["correctness", "cost", "latency"])
    by_name = {c.name: c for c in p.criteria}
    assert by_name["correctness"].direction == "benefit"
    assert by_name["cost"].direction == "cost"
    assert by_name["latency"].direction == "cost"


def test_build_profile_explicit_cost_axes_override() -> None:
    p = build_criterion_profile(["x", "y"], cost_axes=["x"])
    by_name = {c.name: c for c in p.criteria}
    assert by_name["x"].direction == "cost"
    assert by_name["y"].direction == "benefit"


def test_build_profile_mismatched_weights_rejected() -> None:
    with pytest.raises(TopsisError, match="length"):
        build_criterion_profile(["a", "b"], weights=[1.0])


def test_build_profile_empty_criteria_rejected() -> None:
    with pytest.raises(TopsisError, match="non-empty"):
        build_criterion_profile([])


# ---------------------------------------------------------------------------
# parse_criteria_csv
# ---------------------------------------------------------------------------


def test_parse_csv_basic() -> None:
    assert parse_criteria_csv("a,b,c") == ("a", "b", "c")


def test_parse_csv_strips_whitespace() -> None:
    assert parse_criteria_csv(" a ,  b,c ") == ("a", "b", "c")


def test_parse_csv_rejects_empty_token() -> None:
    with pytest.raises(TopsisError, match="empty"):
        parse_criteria_csv("a,,b")


def test_parse_csv_rejects_blank_input() -> None:
    with pytest.raises(TopsisError, match="non-empty"):
        parse_criteria_csv("   ")


# ---------------------------------------------------------------------------
# rank_candidates — degenerate cases
# ---------------------------------------------------------------------------


def test_empty_candidates_returns_empty_list() -> None:
    assert rank_candidates([], ["a"]) == []


def test_one_candidate_returns_rank_one_with_unit_closeness() -> None:
    result = rank_candidates([_cand("only", a=0.4)], ["a"])
    assert len(result) == 1
    assert result[0].key == "only"
    assert result[0].rank == 1
    assert result[0].closeness == pytest.approx(1.0)


def test_one_candidate_missing_axis_rejected() -> None:
    with pytest.raises(TopsisError, match="missing score"):
        rank_candidates([_cand("only")], ["x"])


# ---------------------------------------------------------------------------
# rank_candidates — input validation
# ---------------------------------------------------------------------------


def test_duplicate_keys_rejected() -> None:
    with pytest.raises(TopsisError, match="Duplicate candidate"):
        rank_candidates(
            [_cand("a", x=1.0), _cand("a", x=2.0)],
            ["x"],
        )


def test_nan_score_rejected() -> None:
    with pytest.raises(TopsisError, match="non-finite"):
        rank_candidates(
            [_cand("a", x=float("nan")), _cand("b", x=1.0)],
            ["x"],
        )


def test_infinite_score_rejected() -> None:
    with pytest.raises(TopsisError, match="non-finite"):
        rank_candidates(
            [_cand("a", x=float("inf")), _cand("b", x=1.0)],
            ["x"],
        )


def test_missing_criterion_in_candidate_rejected() -> None:
    with pytest.raises(TopsisError, match="missing score"):
        rank_candidates(
            [_cand("a", x=1.0), _cand("b", y=2.0)],
            ["x", "y"],
        )


def test_boolean_score_rejected() -> None:
    with pytest.raises(TopsisError, match="real number"):
        rank_candidates(
            [Candidate("a", {"x": True}), _cand("b", x=1.0)],  # type: ignore[dict-item]
            ["x"],
        )


def test_string_score_rejected() -> None:
    with pytest.raises(TopsisError, match="real number"):
        rank_candidates(
            [Candidate("a", {"x": "hi"}), _cand("b", x=1.0)],  # type: ignore[dict-item]
            ["x"],
        )


def test_profile_and_weights_together_rejected() -> None:
    profile = build_criterion_profile(["a"])
    with pytest.raises(TopsisError, match="weights cannot be combined"):
        rank_candidates(
            [_cand("k", a=1.0)],
            profile,
            weights=[1.0],
        )


def test_zero_weight_sum_rejected() -> None:
    profile = CriterionProfile(criteria=(Criterion("a", weight=0.0), Criterion("b", weight=0.0)))
    with pytest.raises(TopsisError, match="weights must be positive"):
        rank_candidates(
            [_cand("p", a=1.0, b=2.0), _cand("q", a=2.0, b=1.0)],
            profile,
        )


# ---------------------------------------------------------------------------
# rank_candidates — 2-candidate sanity checks
# ---------------------------------------------------------------------------


def test_two_candidates_benefit_axis_higher_wins() -> None:
    result = rank_candidates(
        [_cand("a", x=1.0), _cand("b", x=2.0)],
        ["x"],
    )
    assert result[0].key == "b"
    assert result[0].rank == 1


def test_two_candidates_cost_axis_lower_wins() -> None:
    profile = build_criterion_profile(["cost"])
    result = rank_candidates(
        [_cand("a", cost=10.0), _cand("b", cost=1.0)],
        profile,
    )
    assert result[0].key == "b"


def test_two_candidates_all_equal_stable_order() -> None:
    result = rank_candidates(
        [_cand("z", x=1.0), _cand("a", x=1.0)],
        ["x"],
    )
    # Stable ascending key tie-break.
    assert [r.key for r in result] == ["a", "z"]
    assert result[0].closeness == result[1].closeness


def test_two_candidates_with_weights() -> None:
    profile = build_criterion_profile(["safety", "speed"], weights=[10.0, 1.0])
    result = rank_candidates(
        [_cand("s", safety=1.0, speed=0.1), _cand("f", safety=0.5, speed=1.0)],
        profile,
    )
    assert result[0].key == "s"  # safety wins because of weight


# ---------------------------------------------------------------------------
# rank_candidates — 5-candidate fixtures
# ---------------------------------------------------------------------------


def test_five_candidates_strict_ordering() -> None:
    result = rank_candidates(
        [_cand(f"c{i}", x=float(i)) for i in range(5)],
        ["x"],
    )
    keys = [r.key for r in result]
    assert keys == ["c4", "c3", "c2", "c1", "c0"]
    for i, r in enumerate(result, start=1):
        assert r.rank == i


def test_five_candidates_multi_axis_safety_first() -> None:
    # Profile: correctness completely dominates (1000x weight).
    profile = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=1000.0),
            Criterion("cost", direction="cost", weight=1.0),
        )
    )
    candidates = [
        _cand("safe-expensive", correctness=1.0, cost=10.0),
        _cand("balanced", correctness=0.8, cost=5.0),
        _cand("risky-cheap", correctness=0.5, cost=1.0),
        _cand("safer-mid", correctness=0.95, cost=7.0),
        _cand("middle", correctness=0.7, cost=4.0),
    ]
    result = rank_candidates(candidates, profile)
    assert result[0].key == "safe-expensive"


def test_five_candidates_speed_first_profile_picks_different_winner() -> None:
    # Same candidate set, latency-first.
    profile_speed = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=1.0),
            Criterion("latency", direction="cost", weight=10.0),
        )
    )
    candidates = [
        _cand("a", correctness=0.9, latency=100.0),
        _cand("b", correctness=0.6, latency=2.0),
        _cand("c", correctness=0.8, latency=20.0),
    ]
    result = rank_candidates(candidates, profile_speed)
    assert result[0].key == "b"


# ---------------------------------------------------------------------------
# rank_candidates — output structure
# ---------------------------------------------------------------------------


def test_result_includes_normalised_scores_for_each_axis() -> None:
    result = rank_candidates(
        [_cand("a", x=1.0, y=2.0), _cand("b", x=2.0, y=1.0)],
        ["x", "y"],
    )
    for r in result:
        assert "x" in r.normalised_scores
        assert "y" in r.normalised_scores
        assert all(math.isfinite(v) for v in r.normalised_scores.values())


def test_result_rank_sequence_is_one_through_n() -> None:
    result = rank_candidates(
        [_cand(f"c{i}", x=float(i)) for i in range(4)],
        ["x"],
    )
    assert [r.rank for r in result] == [1, 2, 3, 4]


def test_result_closeness_in_unit_interval() -> None:
    result = rank_candidates(
        [_cand("a", x=1.0, y=2.0), _cand("b", x=2.0, y=1.0)],
        ["x", "y"],
    )
    assert all(0.0 <= r.closeness <= 1.0 for r in result)


def test_result_descending_closeness() -> None:
    result = rank_candidates(
        [
            _cand("c1", x=1.0),
            _cand("c3", x=3.0),
            _cand("c2", x=2.0),
            _cand("c4", x=4.0),
        ],
        ["x"],
    )
    closenesses = [r.closeness for r in result]
    assert closenesses == sorted(closenesses, reverse=True)


# ---------------------------------------------------------------------------
# Identity weights default + deterministic winner
# ---------------------------------------------------------------------------


def test_identity_weights_used_when_not_specified() -> None:
    """Identity weights — the default required by the issue spec."""
    result = rank_candidates(
        [
            _cand("a", x=1.0, y=10.0),
            _cand("b", x=10.0, y=1.0),
        ],
        ["x", "y"],
    )
    # Symmetric scores under identity weights — keys break the tie.
    assert result[0].closeness == pytest.approx(result[1].closeness)
    assert [r.key for r in result] == ["a", "b"]


def test_deterministic_winner_over_100_invocations() -> None:
    """Issue acceptance: same inputs produce the same winner."""
    candidates = [
        _cand("alpha", x=0.5, y=0.4),
        _cand("beta", x=0.9, y=0.2),
        _cand("gamma", x=0.7, y=0.8),
    ]
    winners = {rank_candidates(candidates, ["x", "y"])[0].key for _ in range(100)}
    assert len(winners) == 1


def test_winner_changes_with_profile() -> None:
    """Issue acceptance: same candidates + different profile picks
    a different winner."""
    candidates = [
        _cand("alpha", correctness=1.0, cost=10.0),
        _cand("beta", correctness=0.6, cost=1.0),
    ]
    safety = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=10.0),
            Criterion("cost", direction="cost", weight=1.0),
        )
    )
    speed = CriterionProfile(
        criteria=(
            Criterion("correctness", weight=1.0),
            Criterion("cost", direction="cost", weight=10.0),
        )
    )
    w_safety = rank_candidates(candidates, safety)[0].key
    w_speed = rank_candidates(candidates, speed)[0].key
    assert w_safety != w_speed
    assert w_safety == "alpha"
    assert w_speed == "beta"


# ---------------------------------------------------------------------------
# Direction handling
# ---------------------------------------------------------------------------


def test_cost_axis_inverts_preference() -> None:
    benefit_profile = build_criterion_profile(["x"], cost_axes=[])
    cost_profile = build_criterion_profile(["x"], cost_axes=["x"])
    cands = [_cand("a", x=1.0), _cand("b", x=2.0)]
    assert rank_candidates(cands, benefit_profile)[0].key == "b"
    assert rank_candidates(cands, cost_profile)[0].key == "a"


def test_mixed_direction_profile() -> None:
    profile = CriterionProfile(
        criteria=(
            Criterion("benefit_axis"),
            Criterion("cost_axis", direction="cost"),
        )
    )
    cands = [
        _cand("good", benefit_axis=10.0, cost_axis=1.0),
        _cand("bad", benefit_axis=1.0, cost_axis=10.0),
    ]
    assert rank_candidates(cands, profile)[0].key == "good"


# ---------------------------------------------------------------------------
# Zero-column edge case
# ---------------------------------------------------------------------------


def test_zero_column_does_not_break_ranking() -> None:
    """A column that is identically zero contributes nothing — the
    remaining axes still produce a sensible ranking."""
    result = rank_candidates(
        [
            _cand("a", live=1.0, dead=0.0),
            _cand("b", live=2.0, dead=0.0),
        ],
        ["live", "dead"],
    )
    assert result[0].key == "b"


def test_zero_column_with_zero_weight_axis() -> None:
    profile = CriterionProfile(
        criteria=(
            Criterion("strong", weight=1.0),
            Criterion("ignored", weight=0.0),
        )
    )
    result = rank_candidates(
        [
            _cand("a", strong=1.0, ignored=999.0),
            _cand("b", strong=2.0, ignored=-999.0),
        ],
        profile,
    )
    assert result[0].key == "b"


# ---------------------------------------------------------------------------
# Weight semantics
# ---------------------------------------------------------------------------


def test_weight_sum_non_normalised_is_renormalised_internally() -> None:
    # Two equivalent profiles — only differ in weight scale.
    p_small = build_criterion_profile(["a", "b"], weights=[0.5, 0.5])
    p_large = build_criterion_profile(["a", "b"], weights=[50.0, 50.0])
    cands = [_cand("x", a=1.0, b=2.0), _cand("y", a=2.0, b=1.0)]
    r1 = rank_candidates(cands, p_small)
    r2 = rank_candidates(cands, p_large)
    assert [r.key for r in r1] == [r.key for r in r2]
    for a, b in zip(r1, r2, strict=True):
        assert a.closeness == pytest.approx(b.closeness)


def test_all_zero_weights_except_one_concentrates_ranking() -> None:
    profile = CriterionProfile(
        criteria=(
            Criterion("dominant", weight=1.0),
            Criterion("ignored1", weight=0.0),
            Criterion("ignored2", weight=0.0),
        )
    )
    cands = [
        _cand("a", dominant=0.1, ignored1=99.0, ignored2=99.0),
        _cand("b", dominant=0.9, ignored1=0.01, ignored2=0.01),
    ]
    assert rank_candidates(cands, profile)[0].key == "b"


# ---------------------------------------------------------------------------
# Permutation invariance (input order does not matter)
# ---------------------------------------------------------------------------


def test_input_permutation_does_not_change_winner() -> None:
    base = [
        _cand("a", x=0.3, y=0.7),
        _cand("b", x=0.9, y=0.2),
        _cand("c", x=0.5, y=0.5),
    ]
    reversed_inputs = list(reversed(base))
    w1 = rank_candidates(base, ["x", "y"])[0].key
    w2 = rank_candidates(reversed_inputs, ["x", "y"])[0].key
    assert w1 == w2


def test_input_permutation_preserves_closeness_set() -> None:
    base = [
        _cand("a", x=0.3, y=0.7),
        _cand("b", x=0.9, y=0.2),
        _cand("c", x=0.5, y=0.5),
    ]
    reversed_inputs = list(reversed(base))
    set1 = {(r.key, round(r.closeness, 9)) for r in rank_candidates(base, ["x", "y"])}
    set2 = {(r.key, round(r.closeness, 9)) for r in rank_candidates(reversed_inputs, ["x", "y"])}
    assert set1 == set2


# ---------------------------------------------------------------------------
# Scale invariance
# ---------------------------------------------------------------------------


def test_uniform_scaling_of_scores_preserves_ranking() -> None:
    base = [_cand("a", x=1.0, y=2.0), _cand("b", x=2.0, y=1.0), _cand("c", x=3.0, y=3.0)]
    scaled = [Candidate(c.key, {k: v * 1000.0 for k, v in c.scores.items()}) for c in base]
    keys1 = [r.key for r in rank_candidates(base, ["x", "y"])]
    keys2 = [r.key for r in rank_candidates(scaled, ["x", "y"])]
    assert keys1 == keys2


# ---------------------------------------------------------------------------
# Extra axes in candidate are ignored
# ---------------------------------------------------------------------------


def test_extra_axes_in_candidate_ignored() -> None:
    result = rank_candidates(
        [
            Candidate("a", {"x": 1.0, "secret": 100.0}),
            Candidate("b", {"x": 2.0, "secret": -100.0}),
        ],
        ["x"],
    )
    # Ranking driven purely by x.
    assert result[0].key == "b"


# ---------------------------------------------------------------------------
# Return-type contract
# ---------------------------------------------------------------------------


def test_result_is_list_of_ranked_candidate_instances() -> None:
    result = rank_candidates(
        [_cand("a", x=1.0), _cand("b", x=2.0)],
        ["x"],
    )
    assert all(isinstance(r, RankedCandidate) for r in result)
