"""Property tests for the criterion-profile module (issue #1346).

These tests assert invariants that must hold across the entire input
space rather than for hand-picked fixtures.  They focus on three
classes of bug that hand-rolled examples are bad at finding:

* **Validation soundness**: every vector that passes :func:`from_dict`
  also re-validates via :meth:`CriterionProfile.validate`.  Likewise,
  every vector that fails validation never succeeds via
  :func:`resolve` or :func:`from_dict`.

* **Bias determinism**: :func:`derive_bias` is a pure function of the
  weight vector.  Running it twice on the same input yields the
  exact same :class:`RoutingBias` (same string fields and same
  ``max_blast_radius``).

* **Normalisation idempotence**: a profile already on the simplex
  passes through :func:`normalize` unchanged (within float epsilon).

All properties use the ``smoke`` Hypothesis profile (50 examples) so
the file runs in well under 10 s on a GitHub-hosted runner.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from bernstein.core.routing.criterion_profile import (
    AXES,
    CRITERION_PROFILE_REGISTRY,
    CriterionProfile,
    CriterionProfileError,
    derive_bias,
    extract_from_task,
    from_dict,
    inherit_for_child,
    normalize,
    resolve,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_simplex_floats = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)


@st.composite
def _valid_simplex_vector(draw: st.DrawFn) -> dict[str, float]:
    """Draw a valid weight vector that sums to 1.0 (within tolerance)."""
    # Draw four non-negative floats then rescale.  Avoid the all-zero
    # case which cannot be rescaled.
    raw = [draw(_simplex_floats) for _ in AXES]
    total = sum(raw)
    assume(total > 1e-6)
    rescaled = [x / total for x in raw]
    return dict(zip(AXES, rescaled, strict=True))


@st.composite
def _arbitrary_vector(draw: st.DrawFn) -> dict[str, float]:
    """Draw an arbitrary non-negative weight vector (NOT rescaled)."""
    return {axis: draw(_simplex_floats) for axis in AXES}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(_valid_simplex_vector())
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_valid_vectors_round_trip_through_from_dict(raw: dict[str, float]) -> None:
    """Every vector on the simplex constructs and re-validates."""
    profile = from_dict(raw)
    profile.validate()
    out = profile.as_dict()
    for axis in AXES:
        assert out[axis] == pytest.approx(raw[axis], abs=1e-9)


@given(_valid_simplex_vector())
def test_derive_bias_is_pure(raw: dict[str, float]) -> None:
    """``derive_bias`` returns equal results for the same input."""
    profile = from_dict(raw)
    a = derive_bias(profile)
    b = derive_bias(profile)
    assert a == b


@given(_valid_simplex_vector())
def test_derive_bias_forced_model_is_in_known_set(raw: dict[str, float]) -> None:
    """The forced_model is always one of the documented tier-anchors."""
    profile = from_dict(raw)
    bias = derive_bias(profile)
    assert bias.forced_model in {"opus", "sonnet", "haiku"}


@given(_valid_simplex_vector())
def test_dominant_axis_matches_max_weight(raw: dict[str, float]) -> None:
    """The dominant axis is always the one with the maximal weight."""
    profile = from_dict(raw)
    dominant = profile.dominant_axis()
    weights = profile.as_dict()
    assert weights[dominant] == max(weights.values())


@given(_arbitrary_vector())
def test_normalize_produces_unit_sum(raw: dict[str, float]) -> None:
    """The output of normalize sums to 1.0 within tolerance for any nonzero input."""
    total = sum(raw.values())
    assume(total > 1e-9)
    profile = normalize(raw)
    s = sum(profile.as_dict().values())
    assert math.isclose(s, 1.0, abs_tol=1e-6)


@given(_valid_simplex_vector())
def test_normalize_idempotent_on_simplex(raw: dict[str, float]) -> None:
    """A vector already on the simplex passes through normalize unchanged."""
    once = normalize(raw)
    twice = normalize(once.as_dict())
    for axis in AXES:
        assert math.isclose(getattr(once, axis), getattr(twice, axis), abs_tol=1e-9)


@given(_arbitrary_vector())
def test_arbitrary_vector_either_validates_or_raises(
    raw: dict[str, float],
) -> None:
    """``from_dict`` is total: it either returns a validated profile or raises."""
    try:
        profile = from_dict(raw)
    except CriterionProfileError:
        return
    # If we got a profile back, re-validation must succeed too.
    profile.validate()


@given(st.sampled_from(list(CRITERION_PROFILE_REGISTRY.keys())))
def test_all_registered_presets_have_valid_bias(name: str) -> None:
    """Every preset resolves and yields a non-empty bias rationale."""
    profile = resolve(name)
    bias = derive_bias(profile)
    assert bias.rationale != ""
    assert bias.forced_model is not None


@given(st.sampled_from(list(CRITERION_PROFILE_REGISTRY.keys())))
def test_inheritance_from_parent_preserves_name(name: str) -> None:
    """Child inherits the parent's preset spec verbatim."""
    out = inherit_for_child({"criterion_profile": name}, None)
    assert out["criterion_profile"] == name


@given(_valid_simplex_vector(), _valid_simplex_vector())
def test_child_override_always_wins(parent_raw: dict[str, float], child_raw: dict[str, float]) -> None:
    """A child profile with an explicit spec is never overwritten by the parent."""
    parent_md = {"criterion_profile": parent_raw}
    child_md = {"criterion_profile": child_raw}
    merged = inherit_for_child(parent_md, child_md)
    assert merged["criterion_profile"] == child_raw


@given(_valid_simplex_vector())
def test_extract_from_task_via_inline_dict(raw: dict[str, float]) -> None:
    """A task carrying an inline dict resolves to the same vector."""

    class _Task:
        id = "T-prop"
        metadata = {"criterion_profile": raw}

    profile = extract_from_task(_Task())
    assert profile is not None
    for axis in AXES:
        assert getattr(profile, axis) == pytest.approx(raw[axis], abs=1e-9)


@given(_valid_simplex_vector())
def test_bias_for_correctness_dominant_is_opus(raw: dict[str, float]) -> None:
    """When correctness >= 0.5 the bias always pins to opus."""
    # Skew the vector so correctness dominates without breaking the
    # simplex; then re-check via the actual derive_bias call.
    assume(raw["correctness"] >= 0.5)
    assume(raw["reversibility"] < 0.5)
    profile = CriterionProfile(
        correctness=raw["correctness"],
        cost=raw["cost"],
        latency=raw["latency"],
        reversibility=raw["reversibility"],
    )
    bias = derive_bias(profile)
    assert bias.forced_model == "opus"


@given(_valid_simplex_vector())
def test_profile_is_frozen_dataclass(raw: dict[str, float]) -> None:
    """The dataclass is frozen so accidental mutation raises ``FrozenInstanceError``."""
    from dataclasses import FrozenInstanceError

    profile = from_dict(raw)
    with pytest.raises(FrozenInstanceError):
        profile.correctness = 0.0  # type: ignore[misc]
