"""Surgical tests for ``bandit_router`` internal math + loader dark paths.

The public ``BanditRouter`` / ``BanditPolicy`` surface is exercised by
``tests/unit/test_bandit_router.py``; this module targets the remaining
uncovered helpers - pure linear-algebra primitives, JSON-load coercion,
matrix validation, and the cold-start static selectors. All of these are
RNG-free, so every assertion is deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bernstein.core.models import Complexity, Scope, Task, TaskType

from bernstein.core.routing import bandit_router as br
from bernstein.core.routing.bandit_router import (
    FEATURE_DIM,
    ArmScore,
    BanditPolicy,
    TaskContext,
    _arm_score_payload,
    _capability_floor,
    _clamp_to_floor,
    _coerce_int_mapping,
    _dot,
    _effort_for_task,
    _identity,
    _inv,
    _is_high_stakes,
    _is_matrix,
    _is_vector,
    _load_arm_matrices,
    _load_arms,
    _load_exploration_history,
    _matmul_vec,
    _sherman_morrison_update,
    _static_select,
    _validate_raw_matrices,
    compute_reward,
)


def _task(
    role: str = "backend",
    complexity: Complexity = Complexity.MEDIUM,
    scope: Scope = Scope.MEDIUM,
    priority: int = 2,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> Task:
    return Task(
        id="t1",
        title="Do something",
        description="desc",
        role=role,
        complexity=complexity,
        scope=scope,
        priority=priority,
        model=model,
        effort=effort,
        owned_files=[],
        estimated_minutes=30,
        task_type=TaskType.STANDARD,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Linear algebra primitives
# ---------------------------------------------------------------------------


class TestLinearAlgebra:
    def test_identity_shape_and_diagonal(self) -> None:
        m = _identity(3)
        assert m == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    def test_dot_product(self) -> None:
        assert _dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(32.0)

    def test_matmul_vec(self) -> None:
        mat = [[1.0, 0.0], [0.0, 2.0]]
        assert _matmul_vec(mat, [3.0, 4.0]) == pytest.approx([3.0, 8.0])

    def test_inv_of_diagonal(self) -> None:
        inv = _inv([[2.0, 0.0], [0.0, 4.0]])
        assert inv[0][0] == pytest.approx(0.5)
        assert inv[1][1] == pytest.approx(0.25)

    def test_inv_round_trip_identity(self) -> None:
        mat = [[4.0, 7.0], [2.0, 6.0]]
        inv = _inv(mat)
        product = [[_dot(mat[i], [inv[0][j], inv[1][j]]) for j in range(2)] for i in range(2)]
        assert product[0][0] == pytest.approx(1.0, abs=1e-9)
        assert product[1][1] == pytest.approx(1.0, abs=1e-9)
        assert product[0][1] == pytest.approx(0.0, abs=1e-9)

    def test_inv_singular_returns_identity(self) -> None:
        # A row of zeros makes the matrix singular -> fallback to identity.
        singular = [[0.0, 0.0], [0.0, 0.0]]
        assert _inv(singular) == _identity(2)

    def test_sherman_morrison_matches_direct_inverse(self) -> None:
        # (A + x x^T)^-1 via Sherman-Morrison should match a direct inverse.
        a_inv = _identity(2)  # A = I
        x = [1.0, 2.0]
        sm = _sherman_morrison_update(a_inv, x)
        # Direct: A + x x^T = [[2,2],[2,5]]; invert it.
        direct = _inv([[2.0, 2.0], [2.0, 5.0]])
        for i in range(2):
            for j in range(2):
                assert sm[i][j] == pytest.approx(direct[i][j], abs=1e-9)

    def test_sherman_morrison_zero_vector_is_noop(self) -> None:
        a_inv = _identity(3)
        # x = 0 -> denom = 1, update leaves the inverse unchanged.
        assert _sherman_morrison_update(a_inv, [0.0, 0.0, 0.0]) == a_inv


# ---------------------------------------------------------------------------
# JSON load coercion helpers
# ---------------------------------------------------------------------------


class TestLoadArms:
    def test_valid_list_kept(self) -> None:
        assert _load_arms(["haiku", "sonnet"], ["fallback"]) == ["haiku", "sonnet"]

    def test_non_list_returns_fallback(self) -> None:
        assert _load_arms("not-a-list", ["a", "b"]) == ["a", "b"]

    def test_empty_after_filter_returns_fallback(self) -> None:
        # non-string / empty items are dropped; empty result -> fallback.
        assert _load_arms([1, "", None], ["x"]) == ["x"]

    def test_fallback_is_copied(self) -> None:
        fb = ["a"]
        out = _load_arms("bad", fb)
        out.append("b")
        assert fb == ["a"]


class TestVectorMatrixGuards:
    def test_is_vector_correct_length(self) -> None:
        assert _is_vector([0.0] * FEATURE_DIM, FEATURE_DIM) is True

    def test_is_vector_wrong_length(self) -> None:
        assert _is_vector([0.0, 1.0], FEATURE_DIM) is False

    def test_is_vector_non_numeric(self) -> None:
        bad = ["x"] * FEATURE_DIM
        assert _is_vector(bad, FEATURE_DIM) is False

    def test_is_vector_non_list(self) -> None:
        assert _is_vector("nope", FEATURE_DIM) is False

    def test_is_matrix_correct(self) -> None:
        m = _identity(FEATURE_DIM)
        assert _is_matrix(m, FEATURE_DIM) is True

    def test_is_matrix_wrong_dim(self) -> None:
        assert _is_matrix([[1.0, 0.0], [0.0, 1.0]], FEATURE_DIM) is False

    def test_is_matrix_non_list(self) -> None:
        assert _is_matrix(42, FEATURE_DIM) is False


class TestCoerceIntMapping:
    def test_coerces_floats_and_strings(self) -> None:
        assert _coerce_int_mapping({"a": 1.9, "b": "3"}) == {"a": 1, "b": 3}

    def test_non_dict_returns_empty(self) -> None:
        assert _coerce_int_mapping("nope") == {}

    def test_bool_values_skipped(self) -> None:
        # bools are ints in Python but must not be counted.
        assert _coerce_int_mapping({"flag": True, "n": 5}) == {"n": 5}


class TestLoadExplorationHistory:
    def test_initialises_arms_empty(self) -> None:
        out = _load_exploration_history(None, ["haiku", "sonnet"])
        assert out == {"haiku": [], "sonnet": []}

    def test_loads_numeric_samples(self) -> None:
        out = _load_exploration_history({"haiku": [0.1, 0.2]}, ["haiku"])
        assert out["haiku"] == pytest.approx([0.1, 0.2])

    def test_window_truncated_to_limit(self) -> None:
        big = list(range(br._EXPLORATION_HISTORY_LIMIT + 50))
        out = _load_exploration_history({"haiku": big}, ["haiku"])
        # only the most recent _EXPLORATION_HISTORY_LIMIT samples kept.
        assert len(out["haiku"]) == br._EXPLORATION_HISTORY_LIMIT
        assert out["haiku"][-1] == pytest.approx(float(big[-1]))

    def test_non_list_samples_skipped(self) -> None:
        out = _load_exploration_history({"haiku": "garbage"}, ["haiku"])
        assert out["haiku"] == []


# ---------------------------------------------------------------------------
# Matrix validation + load
# ---------------------------------------------------------------------------


class TestValidateRawMatrices:
    def test_missing_both_returns_none(self) -> None:
        assert _validate_raw_matrices({}, Path("p")) is None

    def test_invalid_inv_type_returns_none(self) -> None:
        assert _validate_raw_matrices({"A_inv": "bad"}, Path("p")) is None

    def test_invalid_legacy_matrix_type_returns_none(self) -> None:
        assert _validate_raw_matrices({"A": "bad"}, Path("p")) is None

    def test_invalid_vector_type_returns_none(self) -> None:
        assert _validate_raw_matrices({"A_inv": {}, "b": "bad"}, Path("p")) is None

    def test_valid_returns_three_dicts(self) -> None:
        out = _validate_raw_matrices({"A_inv": {"haiku": []}, "b": {"haiku": []}}, Path("p"))
        assert out is not None
        raw_inv, raw_mat, raw_vec = out
        assert "haiku" in raw_inv
        assert raw_mat == {}
        assert "haiku" in raw_vec


class TestLoadArmMatrices:
    def test_dimension_mismatch_returns_none(self) -> None:
        raw = {"A_inv": {"haiku": [[1.0, 0.0]]}, "b": {"haiku": [0.0, 0.0]}}
        assert _load_arm_matrices(raw, ["haiku"], Path("p")) is None

    def test_valid_inv_loads_directly(self) -> None:
        ident = _identity(FEATURE_DIM)
        raw = {"A_inv": {"haiku": ident}, "b": {"haiku": [0.0] * FEATURE_DIM}}
        out = _load_arm_matrices(raw, ["haiku"], Path("p"))
        assert out is not None
        loaded_inv, loaded_vec, legacy = out
        assert legacy is False
        assert loaded_inv["haiku"] == ident
        assert loaded_vec["haiku"] == [0.0] * FEATURE_DIM

    def test_legacy_matrix_sets_legacy_flag(self) -> None:
        # Only legacy "A" present -> loader inverts and flags legacy.
        ident = _identity(FEATURE_DIM)
        raw = {"A": {"haiku": ident}, "b": {"haiku": [0.0] * FEATURE_DIM}}
        out = _load_arm_matrices(raw, ["haiku"], Path("p"))
        assert out is not None
        _, _, legacy = out
        assert legacy is True


# ---------------------------------------------------------------------------
# ArmScore payload
# ---------------------------------------------------------------------------


class TestArmScorePayload:
    def test_none_returns_none(self) -> None:
        assert _arm_score_payload(None) is None

    def test_rounds_components(self) -> None:
        payload = _arm_score_payload(ArmScore(arm="haiku", exploit=0.1234567, explore=0.7654321, total=0.8888888))
        assert payload == {"exploit": 0.123457, "explore": 0.765432, "total": 0.888889}


# ---------------------------------------------------------------------------
# compute_reward (composite quality x cost)
# ---------------------------------------------------------------------------


class TestComputeReward:
    def test_perfect_quality_no_budget_returns_quality(self) -> None:
        assert compute_reward(1.0, cost_usd=100.0, budget_ceiling=0.0) == pytest.approx(1.0)

    def test_negative_budget_skips_normalisation(self) -> None:
        assert compute_reward(0.5, cost_usd=100.0, budget_ceiling=-1.0) == pytest.approx(0.5)

    def test_half_cost_halves_reward(self) -> None:
        # quality 1.0, cost = 50% of ceiling -> reward 0.5.
        assert compute_reward(1.0, cost_usd=0.5, budget_ceiling=1.0) == pytest.approx(0.5)

    def test_cost_over_ceiling_zero_reward(self) -> None:
        assert compute_reward(1.0, cost_usd=5.0, budget_ceiling=1.0) == pytest.approx(0.0)

    def test_quality_clamped_above_one(self) -> None:
        assert compute_reward(2.0, cost_usd=0.0, budget_ceiling=1.0) == pytest.approx(1.0)

    def test_quality_clamped_below_zero(self) -> None:
        assert compute_reward(-1.0, cost_usd=0.0, budget_ceiling=1.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Static cold-start selectors
# ---------------------------------------------------------------------------


class TestStaticSelectors:
    def test_high_stakes_starts_at_sonnet(self) -> None:
        model, reason = _static_select(_task(role="security"))
        assert model == "sonnet"
        assert "high-stakes" in reason

    def test_manager_override_when_valid_arm(self) -> None:
        model, reason = _static_select(_task(role="backend", model="opus"))
        assert model == "opus"
        assert reason == "manager override"

    def test_manager_override_invalid_arm_falls_to_haiku(self) -> None:
        # a model not in _DEFAULT_ARMS is ignored -> default cheapest.
        model, _ = _static_select(_task(role="backend", model="some-other-model"))
        assert model == "haiku"

    def test_default_cheapest_is_haiku(self) -> None:
        model, reason = _static_select(_task(role="backend"))
        assert model == "haiku"
        assert "cheapest" in reason

    def test_is_high_stakes_for_high_complexity(self) -> None:
        assert _is_high_stakes(_task(complexity=Complexity.HIGH)) is True

    def test_is_high_stakes_for_large_scope(self) -> None:
        assert _is_high_stakes(_task(scope=Scope.LARGE)) is True

    def test_is_high_stakes_priority_one(self) -> None:
        assert _is_high_stakes(_task(priority=1)) is True

    def test_not_high_stakes_for_plain_task(self) -> None:
        assert _is_high_stakes(_task()) is False


class TestEffortForTask:
    def test_manager_effort_override_wins(self) -> None:
        assert _effort_for_task("haiku", _task(effort="max")) == "max"

    def test_opus_defaults_max(self) -> None:
        assert _effort_for_task("opus", _task()) == "max"

    def test_haiku_defaults_low(self) -> None:
        assert _effort_for_task("haiku", _task()) == "low"

    def test_other_models_default_high(self) -> None:
        assert _effort_for_task("sonnet", _task()) == "high"


class TestCapabilityFloorAndClamp:
    def test_opus_floor_for_high_stakes_high_large(self) -> None:
        floor = _capability_floor(_task(role="architect", complexity=Complexity.HIGH, scope=Scope.LARGE))
        assert floor == "opus"

    def test_sonnet_floor_for_high_stakes(self) -> None:
        assert _capability_floor(_task(role="security")) == "sonnet"

    def test_haiku_floor_for_plain(self) -> None:
        assert _capability_floor(_task()) == "haiku"

    def test_clamp_below_floor_raises(self) -> None:
        model, clamped = _clamp_to_floor("haiku", "sonnet")
        assert model == "sonnet"
        assert clamped is True

    def test_clamp_above_floor_passthrough(self) -> None:
        model, clamped = _clamp_to_floor("opus", "sonnet")
        assert model == "opus"
        assert clamped is False

    def test_clamp_unknown_arm_passthrough(self) -> None:
        model, clamped = _clamp_to_floor("custom-model", "sonnet")
        assert model == "custom-model"
        assert clamped is False


# ---------------------------------------------------------------------------
# BanditPolicy seed_arm refusal + persistence edge
# ---------------------------------------------------------------------------


class TestSeedArmAndPersistence:
    def test_seed_arm_creates_new_arm(self) -> None:
        policy = BanditPolicy(arms=["haiku"])
        policy.seed_arm("opus", mean_reward=0.9)
        assert "opus" in policy.arms

    def test_seed_arm_refuses_when_live_signal(self) -> None:
        policy = BanditPolicy(arms=["haiku"])
        ctx = TaskContext.from_task(_task())
        policy.update("haiku", ctx, reward=1.0)
        b_before = list(policy._b["haiku"])
        policy.seed_arm("haiku", mean_reward=0.1)
        # the live signal must not be overwritten by the prior.
        assert policy._b["haiku"] == b_before

    def test_seed_arm_shifts_bias_feature(self) -> None:
        policy = BanditPolicy(arms=["haiku"])
        policy.seed_arm("haiku", mean_reward=0.8, virtual_observations=5)
        bias_idx = 5
        # b[bias] = n * clamped = 5 * 0.8 = 4.0.
        assert policy._b["haiku"][bias_idx] == pytest.approx(4.0)
        # A_inv[bias,bias] = 1/(1+n) = 1/6.
        assert policy._A_inv["haiku"][bias_idx][bias_idx] == pytest.approx(1.0 / 6.0)

    def test_update_lazily_adds_unknown_arm(self) -> None:
        policy = BanditPolicy(arms=["haiku"])
        ctx = TaskContext.from_task(_task())
        policy.update("brand-new-arm", ctx, reward=1.0)
        assert "brand-new-arm" in policy.arms
        assert policy.total_updates == 1

    def test_load_non_dict_json_returns_fresh(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.json"
        path.write_text(json.dumps([1, 2, 3]))
        policy = BanditPolicy.load(path, arms=["haiku", "sonnet"])
        # falls back to a fresh policy with the given arms.
        assert policy.arms == ["haiku", "sonnet"]
        assert policy.total_updates == 0

    def test_load_corrupt_json_returns_fresh(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.json"
        path.write_text("{ not json")
        policy = BanditPolicy.load(path, arms=["haiku"])
        assert policy.arms == ["haiku"]
