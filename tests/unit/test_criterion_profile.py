"""Unit tests for per-task criterion profile (issue #1346).

Coverage targets:

* The four-axis dataclass invariants - non-negative weights, sum to
  1.0 within tolerance, NaN/inf rejection.
* The three construction paths - :func:`from_dict`, :func:`resolve`
  (named preset / mapping / CriterionProfile pass-through), and the
  YAML loader.
* The router-bias mapping - each dominant-axis branch and the
  no-dominant-axis fallback.
* Task-side helpers - :func:`extract_from_task` for missing /
  malformed / valid metadata; :func:`inherit_for_child` for both
  inheritance and override paths.
* Feature flag honour via the ``BERNSTEIN_CRITERION_PROFILE`` env var.
* Regression: weight overflow and NaN propagation - guard against the
  one concrete bug class the feature could introduce.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.routing.criterion_profile import (
    AXES,
    CRITERION_PROFILE_REGISTRY,
    ENV_FLAG,
    INLINE_PRESET_NAME,
    SUM_TOLERANCE,
    CriterionProfile,
    CriterionProfileError,
    derive_bias,
    describe,
    extract_from_task,
    from_dict,
    inherit_for_child,
    install_loaded_profiles,
    is_enabled,
    load_profiles_from_dir,
    normalize,
    replace_in_registry,
    resolve,
)


@dataclass
class _FakeTask:
    """Light Task-stand-in carrying only what the criterion profile cares about."""

    id: str = "T-fake"
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


# ---------------------------------------------------------------------------
# Construction and basic invariants
# ---------------------------------------------------------------------------


class TestCriterionProfileConstruction:
    def test_safety_first_preset_has_documented_shape(self) -> None:
        p = resolve("safety-first")
        assert p.correctness >= 0.5
        assert p.reversibility >= 0.2
        assert p.name == "safety-first"

    def test_speed_first_preset_has_latency_dominant(self) -> None:
        p = resolve("speed-first")
        assert p.latency >= 0.5
        assert p.dominant_axis() == "latency"

    def test_balanced_preset_is_uniform(self) -> None:
        p = resolve("balanced")
        assert p.correctness == pytest.approx(0.25)
        assert p.cost == pytest.approx(0.25)
        assert p.latency == pytest.approx(0.25)
        assert p.reversibility == pytest.approx(0.25)

    def test_cost_first_preset_has_cost_dominant(self) -> None:
        p = resolve("cost-first")
        assert p.dominant_axis() == "cost"

    def test_all_presets_validate(self) -> None:
        for name, profile in CRITERION_PROFILE_REGISTRY.items():
            profile.validate()
            assert profile.name == name, f"preset {name} drifted from its key"

    def test_inline_dict_round_trips_through_from_dict(self) -> None:
        raw = {"correctness": 0.4, "cost": 0.3, "latency": 0.2, "reversibility": 0.1}
        p = from_dict(raw)
        assert p.name == INLINE_PRESET_NAME
        assert p.as_dict() == raw

    def test_from_dict_accepts_int_weights(self) -> None:
        raw = {"correctness": 1, "cost": 0, "latency": 0, "reversibility": 0}
        p = from_dict(raw)
        assert p.correctness == pytest.approx(1.0)

    def test_from_dict_preserves_explicit_name(self) -> None:
        raw = {"correctness": 1.0, "cost": 0.0, "latency": 0.0, "reversibility": 0.0}
        p = from_dict(raw, name="custom-preset")
        assert p.name == "custom-preset"


# ---------------------------------------------------------------------------
# Validation - rejection paths
# ---------------------------------------------------------------------------


class TestCriterionProfileValidationRejection:
    def test_sum_below_tolerance_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="sum to 1.0"):
            from_dict(
                {
                    "correctness": 0.1,
                    "cost": 0.1,
                    "latency": 0.1,
                    "reversibility": 0.1,
                }
            )

    def test_sum_above_tolerance_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="sum to 1.0"):
            from_dict(
                {
                    "correctness": 0.4,
                    "cost": 0.4,
                    "latency": 0.4,
                    "reversibility": 0.4,
                }
            )

    def test_sum_within_tolerance_accepted(self) -> None:
        # 0.25 + 0.25 + 0.25 + 0.2495 = 0.9995 → within 1e-3 of 1.0.
        from_dict(
            {
                "correctness": 0.25,
                "cost": 0.25,
                "latency": 0.25,
                "reversibility": 0.2495,
            }
        )

    def test_negative_weight_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="non-negative"):
            from_dict(
                {
                    "correctness": 1.1,
                    "cost": -0.1,
                    "latency": 0.0,
                    "reversibility": 0.0,
                }
            )

    def test_nan_weight_raises(self) -> None:
        with pytest.raises(CriterionProfileError):
            CriterionProfile(
                correctness=float("nan"),
                cost=0.0,
                latency=0.0,
                reversibility=1.0,
            ).validate()

    def test_inf_weight_raises(self) -> None:
        with pytest.raises(CriterionProfileError):
            CriterionProfile(
                correctness=float("inf"),
                cost=0.0,
                latency=0.0,
                reversibility=0.0,
            ).validate()

    def test_missing_axis_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="missing required axis"):
            from_dict({"correctness": 1.0, "cost": 0.0, "latency": 0.0})  # type: ignore[arg-type]

    def test_unknown_axis_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="unknown axis"):
            from_dict(
                {
                    "correctness": 0.5,
                    "cost": 0.2,
                    "latency": 0.2,
                    "reversibility": 0.1,
                    "spinach": 0.0,
                }
            )

    def test_non_numeric_weight_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="must be numeric"):
            from_dict(
                {
                    "correctness": "lots",  # type: ignore[dict-item]
                    "cost": 0.0,
                    "latency": 0.0,
                    "reversibility": 0.0,
                }
            )

    def test_boolean_weight_rejected(self) -> None:
        # ``bool`` is a subclass of ``int`` - guard against the silent
        # ``True == 1`` coercion that would let typos through.
        with pytest.raises(CriterionProfileError, match="must be numeric"):
            from_dict(
                {
                    "correctness": True,  # type: ignore[dict-item]
                    "cost": 0.0,
                    "latency": 0.0,
                    "reversibility": 0.0,
                }
            )

    def test_non_mapping_input_rejected(self) -> None:
        with pytest.raises(CriterionProfileError, match="expected mapping"):
            from_dict("safety-first")  # type: ignore[arg-type]

    def test_validate_rejects_bool_via_field(self) -> None:
        # Constructed directly bypassing from_dict → validate still rejects.
        profile = CriterionProfile(
            correctness=True,  # type: ignore[arg-type]
            cost=0.0,
            latency=0.0,
            reversibility=0.0,
        )
        with pytest.raises(CriterionProfileError, match="bool"):
            profile.validate()


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


class TestResolve:
    def test_resolve_named_preset(self) -> None:
        assert resolve("safety-first").name == "safety-first"

    def test_resolve_returns_profile_unchanged(self) -> None:
        original = resolve("balanced")
        out = resolve(original)
        assert out is original

    def test_resolve_inline_dict(self) -> None:
        out = resolve(
            {
                "correctness": 0.4,
                "cost": 0.3,
                "latency": 0.2,
                "reversibility": 0.1,
            }
        )
        assert out.name == INLINE_PRESET_NAME

    def test_resolve_rejects_none(self) -> None:
        with pytest.raises(CriterionProfileError):
            resolve(None)

    def test_resolve_rejects_unknown_preset(self) -> None:
        with pytest.raises(CriterionProfileError, match="unknown criterion preset"):
            resolve("flavour-of-the-month")

    def test_resolve_rejects_empty_preset_name(self) -> None:
        with pytest.raises(CriterionProfileError, match="must not be empty"):
            resolve("")

    def test_resolve_rejects_unsupported_type(self) -> None:
        with pytest.raises(CriterionProfileError, match="unsupported"):
            resolve(42)

    def test_resolve_with_override_registry(self) -> None:
        custom = CriterionProfile(
            correctness=0.7,
            cost=0.1,
            latency=0.1,
            reversibility=0.1,
            name="ops-only",
        )
        registry = {"ops-only": custom}
        assert resolve("ops-only", registry=registry) is custom


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestYamlLoading:
    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_profiles_from_dir(tmp_path / "nope") == {}

    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "operator.yaml").write_text(
            "name: operator\ncorrectness: 0.5\ncost: 0.2\nlatency: 0.2\nreversibility: 0.1\n",
            encoding="utf-8",
        )
        loaded = load_profiles_from_dir(tmp_path)
        assert "operator" in loaded
        assert loaded["operator"].correctness == pytest.approx(0.5)

    def test_malformed_yaml_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text("just a string", encoding="utf-8")
        (tmp_path / "good.yaml").write_text(
            "correctness: 1.0\ncost: 0.0\nlatency: 0.0\nreversibility: 0.0\n",
            encoding="utf-8",
        )
        loaded = load_profiles_from_dir(tmp_path)
        assert "bad" not in loaded
        assert "good" in loaded

    def test_unparseable_yaml_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "garbage.yaml").write_text(
            "::: not yaml :::\n  - [unbalanced\n",
            encoding="utf-8",
        )
        loaded = load_profiles_from_dir(tmp_path)
        assert "garbage" not in loaded

    def test_install_loaded_overrides_registry(self, tmp_path: Path) -> None:
        original = CRITERION_PROFILE_REGISTRY["balanced"]
        try:
            (tmp_path / "balanced.yaml").write_text(
                "name: balanced\ncorrectness: 1.0\ncost: 0.0\nlatency: 0.0\nreversibility: 0.0\n",
                encoding="utf-8",
            )
            install_loaded_profiles(load_profiles_from_dir(tmp_path))
            assert CRITERION_PROFILE_REGISTRY["balanced"].correctness == pytest.approx(1.0)
        finally:
            CRITERION_PROFILE_REGISTRY["balanced"] = original

    def test_bundled_yaml_files_load(self) -> None:
        from bernstein import _BUNDLED_TEMPLATES_DIR  # type: ignore[reportPrivateUsage]

        path = _BUNDLED_TEMPLATES_DIR / "criterion_profiles"
        if not path.is_dir():
            pytest.skip("Bundled criterion_profiles dir missing in this build layout")
        loaded = load_profiles_from_dir(path)
        assert {"safety-first", "speed-first", "balanced", "cost-first"}.issubset(loaded)


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_rescales_to_unit_simplex(self) -> None:
        # 3:1:1:1 → 0.5 / 0.166.. / 0.166.. / 0.166..
        out = normalize({"correctness": 3, "cost": 1, "latency": 1, "reversibility": 1})
        assert out.correctness == pytest.approx(0.5)
        assert out.cost == pytest.approx(1.0 / 6)

    def test_zero_total_raises(self) -> None:
        with pytest.raises(CriterionProfileError, match="zero or negative"):
            normalize({"correctness": 0, "cost": 0, "latency": 0, "reversibility": 0})

    def test_negative_rejected(self) -> None:
        with pytest.raises(CriterionProfileError, match="non-negative"):
            normalize({"correctness": 1, "cost": -1, "latency": 1, "reversibility": 1})

    def test_nan_rejected(self) -> None:
        with pytest.raises(CriterionProfileError, match="finite"):
            normalize(
                {
                    "correctness": float("nan"),
                    "cost": 1,
                    "latency": 1,
                    "reversibility": 1,
                }
            )


# ---------------------------------------------------------------------------
# Bias derivation
# ---------------------------------------------------------------------------


class TestDeriveBias:
    def test_correctness_dominant_pins_opus_max(self) -> None:
        p = resolve("safety-first")
        bias = derive_bias(p)
        assert bias.forced_model == "opus"
        assert bias.forced_effort == "max"

    def test_latency_dominant_pins_haiku_low(self) -> None:
        bias = derive_bias(resolve("speed-first"))
        assert bias.forced_model == "haiku"
        assert bias.forced_effort == "low"

    def test_cost_dominant_pins_haiku_low(self) -> None:
        bias = derive_bias(resolve("cost-first"))
        assert bias.forced_model == "haiku"
        assert bias.forced_effort == "low"

    def test_balanced_falls_back_to_sonnet_high(self) -> None:
        bias = derive_bias(resolve("balanced"))
        assert bias.forced_model == "sonnet"
        assert bias.forced_effort == "high"

    def test_reversibility_dominant_pins_blast_radius(self) -> None:
        p = from_dict(
            {
                "correctness": 0.1,
                "cost": 0.1,
                "latency": 0.1,
                "reversibility": 0.7,
            }
        )
        bias = derive_bias(p)
        assert bias.max_blast_radius == 1
        assert bias.forced_model == "opus"

    def test_rationale_includes_dominant_axis_value(self) -> None:
        bias = derive_bias(resolve("safety-first"))
        assert "correctness" in bias.rationale
        assert "0.60" in bias.rationale

    def test_derive_bias_revalidates(self) -> None:
        # Pass a profile that bypassed from_dict - derive_bias should
        # still complain if it can't be validated.
        bad = CriterionProfile(
            correctness=0.9,
            cost=0.5,
            latency=0.0,
            reversibility=0.0,
        )
        with pytest.raises(CriterionProfileError):
            derive_bias(bad)


# ---------------------------------------------------------------------------
# Task-side helpers
# ---------------------------------------------------------------------------


class TestExtractFromTask:
    def test_returns_none_for_missing_metadata(self) -> None:
        class _Bare: ...

        assert extract_from_task(_Bare()) is None

    def test_returns_none_for_missing_key(self) -> None:
        assert extract_from_task(_FakeTask()) is None

    def test_returns_profile_for_named_preset(self) -> None:
        task = _FakeTask(metadata={"criterion_profile": "safety-first"})
        p = extract_from_task(task)
        assert p is not None
        assert p.name == "safety-first"

    def test_returns_profile_for_inline_dict(self) -> None:
        task = _FakeTask(
            metadata={
                "criterion_profile": {
                    "correctness": 1.0,
                    "cost": 0.0,
                    "latency": 0.0,
                    "reversibility": 0.0,
                },
            }
        )
        p = extract_from_task(task)
        assert p is not None
        assert p.correctness == pytest.approx(1.0)

    def test_malformed_metadata_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        task = _FakeTask(metadata={"criterion_profile": "bogus-preset"})
        with caplog.at_level("WARNING"):
            assert extract_from_task(task) is None
        assert any("invalid criterion_profile" in r.message for r in caplog.records)

    def test_extract_honours_feature_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_FLAG, "0")
        task = _FakeTask(metadata={"criterion_profile": "safety-first"})
        assert extract_from_task(task) is None


class TestInheritForChild:
    def test_child_inherits_when_unset(self) -> None:
        out = inherit_for_child({"criterion_profile": "safety-first"}, None)
        assert out["criterion_profile"] == "safety-first"

    def test_child_override_wins(self) -> None:
        out = inherit_for_child(
            {"criterion_profile": "safety-first"},
            {"criterion_profile": "speed-first"},
        )
        assert out["criterion_profile"] == "speed-first"

    def test_no_parent_no_child(self) -> None:
        assert "criterion_profile" not in inherit_for_child(None, None)

    def test_parent_with_no_profile(self) -> None:
        out = inherit_for_child({}, {"other_field": "x"})
        assert "criterion_profile" not in out
        assert out["other_field"] == "x"

    def test_returns_new_dict(self) -> None:
        parent = {"criterion_profile": "balanced"}
        child = {"foo": "bar"}
        out = inherit_for_child(parent, child)
        assert out is not parent
        assert out is not child


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_default_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_FLAG, raising=False)
        assert is_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "Off"])
    def test_disabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(ENV_FLAG, value)
        assert is_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything-else"])
    def test_enabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(ENV_FLAG, value)
        assert is_enabled() is True

    def test_whitespace_around_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_FLAG, " 0 ")
        assert is_enabled() is False


# ---------------------------------------------------------------------------
# describe()
# ---------------------------------------------------------------------------


class TestDescribe:
    def test_describe_includes_preset_name(self) -> None:
        out = describe(resolve("safety-first"))
        assert "preset=safety-first" in out

    def test_describe_includes_each_axis(self) -> None:
        out = describe(resolve("balanced"))
        for axis in AXES:
            assert axis in out


# ---------------------------------------------------------------------------
# Vector view, dominant axis
# ---------------------------------------------------------------------------


class TestVectorView:
    def test_as_vector_order_matches_axes(self) -> None:
        p = resolve("safety-first")
        vec = p.as_vector()
        assert len(vec) == len(AXES)
        d = p.as_dict()
        for axis, v in zip(AXES, vec, strict=True):
            assert d[axis] == v

    def test_dominant_axis_breaks_ties_deterministically(self) -> None:
        p = from_dict({"correctness": 0.25, "cost": 0.25, "latency": 0.25, "reversibility": 0.25})
        # All four are tied; first wins.
        assert p.dominant_axis() == AXES[0]

    def test_dominant_axis_finds_max(self) -> None:
        p = from_dict({"correctness": 0.1, "cost": 0.6, "latency": 0.2, "reversibility": 0.1})
        assert p.dominant_axis() == "cost"


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------


class TestRegistryHelpers:
    def test_replace_in_registry_round_trips(self) -> None:
        original = CRITERION_PROFILE_REGISTRY["balanced"]
        try:
            updated = replace_in_registry(
                "balanced",
                correctness=1.0,
                cost=0.0,
                latency=0.0,
                reversibility=0.0,
            )
            assert updated.correctness == pytest.approx(1.0)
            assert CRITERION_PROFILE_REGISTRY["balanced"] is updated
        finally:
            CRITERION_PROFILE_REGISTRY["balanced"] = original

    def test_replace_in_registry_rejects_unknown(self) -> None:
        with pytest.raises(KeyError):
            replace_in_registry("does-not-exist", correctness=1.0)


# ---------------------------------------------------------------------------
# Unicode + boundary edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unicode_in_preset_name_via_from_dict(self) -> None:
        out = from_dict(
            {
                "correctness": 1.0,
                "cost": 0.0,
                "latency": 0.0,
                "reversibility": 0.0,
            },
            name="безопасность-prio",
        )
        assert out.name == "безопасность-prio"

    def test_empty_metadata_dict_is_safe(self) -> None:
        assert extract_from_task(_FakeTask(metadata={})) is None

    def test_metadata_not_a_dict_is_safe(self) -> None:
        @dataclass
        class _Weird:
            id: str = "T-weird"
            metadata: str = "not a dict"

        assert extract_from_task(_Weird()) is None  # type: ignore[arg-type]

    def test_oversize_weight_rejected(self) -> None:
        with pytest.raises(CriterionProfileError):
            from_dict(
                {
                    "correctness": 1e6,
                    "cost": 0.0,
                    "latency": 0.0,
                    "reversibility": 0.0,
                }
            )

    def test_single_axis_full_weight_accepted(self) -> None:
        p = from_dict(
            {
                "correctness": 1.0,
                "cost": 0.0,
                "latency": 0.0,
                "reversibility": 0.0,
            }
        )
        assert p.correctness == 1.0


# ---------------------------------------------------------------------------
# Regression: NaN propagation and weight overflow
# ---------------------------------------------------------------------------


class TestRegressionNaNPropagation:
    """Concrete bug class - NaN slipping through validation.

    Earlier drafts validated the sum first and short-circuited on a
    NaN total (since ``nan == anything`` is False, the comparison
    accidentally passed for some NaN combinations).  This regression
    test pins the behaviour: any NaN in any axis must raise.
    """

    def test_nan_in_first_axis_raises(self) -> None:
        with pytest.raises(CriterionProfileError):
            CriterionProfile(
                correctness=math.nan,
                cost=0.5,
                latency=0.3,
                reversibility=0.2,
            ).validate()

    def test_nan_in_last_axis_raises(self) -> None:
        with pytest.raises(CriterionProfileError):
            CriterionProfile(
                correctness=0.5,
                cost=0.3,
                latency=0.2,
                reversibility=math.nan,
            ).validate()

    def test_overflow_weight_caught_via_sum_check(self) -> None:
        # Weight overflow class: extremely large positive numbers should
        # fail the sum check rather than silently routing the task.
        with pytest.raises(CriterionProfileError):
            from_dict(
                {
                    "correctness": 1e308,
                    "cost": 1e308,
                    "latency": 0.0,
                    "reversibility": 0.0,
                }
            )


# ---------------------------------------------------------------------------
# Tolerance constant pinned
# ---------------------------------------------------------------------------


def test_tolerance_constant_matches_documented_value() -> None:
    assert pytest.approx(1e-3) == SUM_TOLERANCE


def test_axes_tuple_is_immutable() -> None:
    assert isinstance(AXES, tuple)
    assert AXES == ("correctness", "cost", "latency", "reversibility")
