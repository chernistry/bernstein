"""Per-task criterion profile with operator-overridable weights (#1346).

A ``CriterionProfile`` is an operator-supplied or preset-derived weight
vector that biases routing decisions on a per-task basis.  Where the
existing :mod:`bernstein.core.routing.mode_profile` controls the agent's
*interaction style* once a model is picked, the criterion profile sits
*ahead* of model selection and answers a different question:

    "For THIS task, how should we trade off correctness vs cost vs
     latency vs reversibility?"

The four axes form a probability simplex (weights sum to 1.0 within a
small tolerance), so an operator can express ``safety-first`` as
``correctness=0.6 cost=0.1 latency=0.1 reversibility=0.2`` and have the
router pin the task to a deep-tier model with a tight blast radius,
while a ``speed-first`` task with ``latency=0.6`` is steered toward a
fast-tier model and skipped past the high-stakes opus override.

The profile is plumbed through three resolution paths:

* **Named preset** - string like ``"safety-first"`` resolved from YAML
  files under ``templates/criterion_profiles/<name>.yaml`` (or any
  registry the loader is pointed at).  Force-included into the wheel
  the same way ``templates/mode_profiles`` is.

* **Inline dict** - ``{"correctness": 0.6, "cost": 0.2, "latency": 0.1,
  "reversibility": 0.1}``.  Useful when an automation needs to set the
  vector programmatically without registering a new preset.

* **Inheritance** - a child task spawned from a parent inherits the
  parent's profile unless the child explicitly overrides it.

Origin: Synapse's per-scenario weight vector
(``time``/``energy``/``safety``/``payload_integrity``) generalised from
drone pathfinding to agent routing.  The same shape (operator picks
a simplex, downstream policies read it as a hard bias) carries over
unchanged; only the axis labels are renamed for the agent-routing
domain.

Feature flag: ``BERNSTEIN_CRITERION_PROFILE=0`` disables resolution at
the call site (see :func:`is_enabled`) and reverts callers to the
pre-existing routing path.  The flag is read fresh on every call so
tests and the CI environment can toggle it without restarting.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

if TYPE_CHECKING:
    from pathlib import Path

from bernstein.core.dataclass_helpers import typed_replace as _typed_replace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Canonical axis names in fixed order.  Order matters for the vector
#: representation but the dict-facing API is order-insensitive.
AXES: Final[tuple[str, ...]] = (
    "correctness",
    "cost",
    "latency",
    "reversibility",
)

#: Tolerance for the "weights sum to 1.0" check.  Generous enough to
#: accept YAML-rounded values like ``0.33/0.33/0.34`` but tight enough
#: to reject obvious operator typos.
SUM_TOLERANCE: Final[float] = 1e-3

#: Sentinel string that :func:`describe` returns when the profile was
#: built from an inline dict rather than a named preset.
INLINE_PRESET_NAME: Final[str] = "inline"

#: Environment variable used as a kill switch.  Set to ``"0"`` (or
#: ``"false"``/``"no"``/``"off"``, case-insensitive) to disable the
#: criterion-profile path.  Any other value (including unset) leaves
#: the feature enabled.
ENV_FLAG: Final[str] = "BERNSTEIN_CRITERION_PROFILE"

#: Values that disable the feature when seen in :data:`ENV_FLAG`.
_DISABLED_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CriterionProfileError(ValueError):
    """Raised when a criterion profile fails validation."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionProfile:
    """Per-task weight vector across (correctness, cost, latency, reversibility).

    Attributes:
        correctness: How much we care about getting the answer right.
            Higher values bias routing toward deep-tier models and stricter
            quality gates.
        cost: How much we care about token spend.  Higher values bias
            toward cheap-tier models and free-tier providers.
        latency: How much we care about wall-clock turnaround.  Higher
            values bias toward fast-tier models and skip the long-context
            deep path.
        reversibility: How easily a mistake can be unwound.  Higher
            values force a smaller blast radius and stricter approval
            gates.
        name: Optional preset name; defaults to the inline sentinel
            when the profile was built from a raw dict.
    """

    correctness: float
    cost: float
    latency: float
    reversibility: float
    name: str = INLINE_PRESET_NAME

    # -- validation ------------------------------------------------------

    def validate(self) -> None:
        """Raise :class:`CriterionProfileError` when the vector is invalid.

        Reject conditions:

        * Any weight is NaN or infinite.
        * Any weight is negative.
        * The sum of all weights diverges from 1.0 by more than
          :data:`SUM_TOLERANCE`.
        """
        for axis in AXES:
            value = getattr(self, axis)
            if not isinstance(value, (int, float)):
                raise CriterionProfileError(f"axis {axis!r} must be numeric, got {type(value).__name__}")
            if isinstance(value, bool):
                # ``bool`` is a subclass of ``int`` - reject explicitly.
                raise CriterionProfileError(f"axis {axis!r} must be numeric, got bool")
            float_value = float(value)
            if math.isnan(float_value):
                raise CriterionProfileError(f"axis {axis!r} is NaN")
            if math.isinf(float_value):
                raise CriterionProfileError(f"axis {axis!r} is infinite")
            if float_value < 0.0:
                raise CriterionProfileError(f"axis {axis!r} must be non-negative, got {float_value}")

        total = self.correctness + self.cost + self.latency + self.reversibility
        if abs(total - 1.0) > SUM_TOLERANCE:
            raise CriterionProfileError(f"weights must sum to 1.0 +/- {SUM_TOLERANCE}, got {total!r}")

    # -- vector view -----------------------------------------------------

    def as_vector(self) -> tuple[float, float, float, float]:
        """Return weights as a tuple in :data:`AXES` order."""
        return (
            self.correctness,
            self.cost,
            self.latency,
            self.reversibility,
        )

    def as_dict(self) -> dict[str, float]:
        """Return weights as a plain dict keyed by axis name."""
        return {
            "correctness": self.correctness,
            "cost": self.cost,
            "latency": self.latency,
            "reversibility": self.reversibility,
        }

    # -- dominant-axis queries ------------------------------------------

    def dominant_axis(self) -> str:
        """Return the axis name with the largest weight.

        Ties are broken in :data:`AXES` order so the result is
        deterministic for any input.
        """
        best_axis = AXES[0]
        best_value = getattr(self, AXES[0])
        for axis in AXES[1:]:
            value = getattr(self, axis)
            if value > best_value:
                best_axis = axis
                best_value = value
        return best_axis

    def is_correctness_dominant(self, threshold: float = 0.5) -> bool:
        """Return ``True`` when correctness weight is at or above *threshold*."""
        return self.correctness >= threshold

    def is_cost_dominant(self, threshold: float = 0.5) -> bool:
        """Return ``True`` when cost weight is at or above *threshold*."""
        return self.cost >= threshold

    def is_latency_dominant(self, threshold: float = 0.5) -> bool:
        """Return ``True`` when latency weight is at or above *threshold*."""
        return self.latency >= threshold

    def is_reversibility_dominant(self, threshold: float = 0.5) -> bool:
        """Return ``True`` when reversibility weight is at or above *threshold*."""
        return self.reversibility >= threshold


# ---------------------------------------------------------------------------
# Bundled defaults
# ---------------------------------------------------------------------------


_SAFETY_FIRST_DEFAULT = CriterionProfile(
    correctness=0.6,
    cost=0.1,
    latency=0.1,
    reversibility=0.2,
    name="safety-first",
)

_SPEED_FIRST_DEFAULT = CriterionProfile(
    correctness=0.2,
    cost=0.1,
    latency=0.6,
    reversibility=0.1,
    name="speed-first",
)

_BALANCED_DEFAULT = CriterionProfile(
    correctness=0.25,
    cost=0.25,
    latency=0.25,
    reversibility=0.25,
    name="balanced",
)

_COST_FIRST_DEFAULT = CriterionProfile(
    correctness=0.1,
    cost=0.6,
    latency=0.2,
    reversibility=0.1,
    name="cost-first",
)


#: In-code default registry.  YAML files loaded via
#: :func:`load_profiles_from_dir` override these by name.
CRITERION_PROFILE_REGISTRY: dict[str, CriterionProfile] = {
    "safety-first": _SAFETY_FIRST_DEFAULT,
    "speed-first": _SPEED_FIRST_DEFAULT,
    "balanced": _BALANCED_DEFAULT,
    "cost-first": _COST_FIRST_DEFAULT,
}


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Return ``True`` when criterion-profile routing is enabled.

    Reads :data:`ENV_FLAG` fresh on every call so callers (notably
    tests) can toggle without process restart.
    """
    raw = os.environ.get(ENV_FLAG)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _extract_weights(raw: object, *, check_finite: bool = False) -> dict[str, float]:
    """Validate *raw* is a four-axis mapping and return its numeric weights.

    Shared parsing for :func:`from_dict` and :func:`normalize`.  Rejects
    non-mapping inputs, missing/extra keys, non-numeric values, and
    booleans.  When ``check_finite`` is set, NaN and inf are also
    rejected up front (``normalize`` needs this because it divides).
    """
    if not isinstance(raw, Mapping):
        raise CriterionProfileError(f"expected mapping, got {type(raw).__name__}")

    raw_mapping: Mapping[str, Any] = cast("Mapping[str, Any]", raw)
    missing = [axis for axis in AXES if axis not in raw_mapping]
    if missing:
        raise CriterionProfileError(f"missing required axis keys: {', '.join(sorted(missing))}")

    extra = [key for key in raw_mapping if key not in AXES]
    if extra:
        raise CriterionProfileError(f"unknown axis keys: {', '.join(sorted(extra))}")

    values: dict[str, float] = {}
    for axis in AXES:
        value: object = raw_mapping[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CriterionProfileError(f"axis {axis!r} must be numeric, got {type(value).__name__}")
        float_value = float(value)
        if check_finite and (math.isnan(float_value) or math.isinf(float_value)):
            raise CriterionProfileError(f"axis {axis!r} must be finite, got {float_value!r}")
        if check_finite and float_value < 0.0:
            raise CriterionProfileError(f"axis {axis!r} must be non-negative, got {float_value}")
        values[axis] = float_value
    return values


def from_dict(
    raw: Mapping[str, Any],
    *,
    name: str = INLINE_PRESET_NAME,
) -> CriterionProfile:
    """Build a :class:`CriterionProfile` from a mapping of axis -> weight.

    Args:
        raw: Mapping that must contain exactly the keys in :data:`AXES`.
            Numeric values are coerced via ``float()``.  Extra keys are
            rejected so that operator typos surface early.
        name: Preset name to embed in the resulting profile.  Defaults
            to the inline sentinel.

    Returns:
        A validated :class:`CriterionProfile`.

    Raises:
        CriterionProfileError: When required keys are missing, extra
            keys are present, values are not numeric, or the resulting
            weights fail :meth:`CriterionProfile.validate`.
    """
    coerced = _extract_weights(raw, check_finite=False)
    profile = CriterionProfile(
        correctness=coerced["correctness"],
        cost=coerced["cost"],
        latency=coerced["latency"],
        reversibility=coerced["reversibility"],
        name=name,
    )
    profile.validate()
    return profile


def normalize(
    raw: Mapping[str, Any],
    *,
    name: str = INLINE_PRESET_NAME,
) -> CriterionProfile:
    """Build a profile from *raw* after rescaling weights to sum to 1.0.

    Unlike :func:`from_dict` this function tolerates weight vectors
    that don't already form a probability simplex, rescaling them by
    their sum.  Still rejects negative weights, NaN/inf, and unknown
    or missing axes - only the sum constraint is relaxed.

    Useful for operator-friendly inputs like ``correctness=3, cost=1,
    latency=1, reversibility=1`` (a 60/20/10/10 vector after normalisation).
    """
    values = _extract_weights(raw, check_finite=True)
    total = sum(values.values())
    if total <= 0.0:
        raise CriterionProfileError("cannot normalise - total weight is zero or negative")

    rescaled = {axis: values[axis] / total for axis in AXES}
    profile = CriterionProfile(
        correctness=rescaled["correctness"],
        cost=rescaled["cost"],
        latency=rescaled["latency"],
        reversibility=rescaled["reversibility"],
        name=name,
    )
    profile.validate()
    return profile


def resolve(
    spec: Any,
    *,
    registry: Mapping[str, CriterionProfile] | None = None,
) -> CriterionProfile:
    """Resolve *spec* into a :class:`CriterionProfile`.

    The single public entry point that callers should use.  Accepts:

    * ``None`` -> raises :class:`CriterionProfileError`.
    * ``str`` -> looked up in *registry* (defaults to the global one).
    * ``CriterionProfile`` -> returned unchanged after re-validation.
    * ``Mapping`` -> forwarded to :func:`from_dict`.

    Any other type raises :class:`CriterionProfileError`.

    Args:
        spec: The raw value plucked from ``task.metadata`` or CLI input.
        registry: Override registry for preset lookup.  Defaults to
            :data:`CRITERION_PROFILE_REGISTRY`.

    Returns:
        A validated :class:`CriterionProfile`.
    """
    if spec is None:
        raise CriterionProfileError("criterion profile spec is None")

    table = registry if registry is not None else CRITERION_PROFILE_REGISTRY

    if isinstance(spec, CriterionProfile):
        spec.validate()
        return spec

    if isinstance(spec, str):
        key = spec.strip()
        if not key:
            raise CriterionProfileError("preset name must not be empty")
        if key not in table:
            raise CriterionProfileError(f"unknown criterion preset: {key!r}")
        profile = table[key]
        # Defensive re-validate so that operator-loaded yaml that snuck
        # into the registry without a sum check still surfaces here.
        profile.validate()
        return profile

    if isinstance(spec, Mapping):
        spec_mapping = cast("Mapping[str, Any]", spec)
        return from_dict(spec_mapping)

    raise CriterionProfileError(f"unsupported criterion profile spec type: {type(spec).__name__}")


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _coerce_profile_yaml(name: str, raw: object) -> CriterionProfile | None:
    """Build a profile from a YAML-decoded value; return ``None`` on bad input."""
    if not isinstance(raw, dict):
        logger.warning("Criterion profile %r is not a mapping; skipped", name)
        return None
    raw_dict = cast("dict[str, Any]", raw)
    try:
        preset_name = str(raw_dict.get("name", name))
        weights: dict[str, Any] = {axis: raw_dict.get(axis) for axis in AXES}
        return from_dict(weights, name=preset_name)
    except CriterionProfileError as exc:
        logger.warning("Invalid criterion profile %r: %s", name, exc)
        return None


def load_profiles_from_dir(path: Path) -> dict[str, CriterionProfile]:
    """Load criterion profiles from ``<path>/*.yaml``.

    Missing directory returns ``{}``.  Each file's stem is used as the
    preset name unless the YAML body provides a ``name:`` key.
    Malformed files are skipped with a warning rather than crashing
    startup.
    """
    if not path.is_dir():
        return {}
    try:
        import yaml
    except ImportError:
        logger.warning(
            "PyYAML not installed; skipping criterion profile load from %s",
            path,
        )
        return {}

    loaded: dict[str, CriterionProfile] = {}
    for yaml_file in sorted(path.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("Failed to read criterion profile %s: %s", yaml_file, exc)
            continue
        profile = _coerce_profile_yaml(yaml_file.stem, raw)
        if profile is not None:
            loaded[profile.name] = profile
    return loaded


def install_loaded_profiles(loaded: Mapping[str, CriterionProfile]) -> None:
    """Merge *loaded* profiles into the module-level registry.

    YAML-defined profiles override the in-code defaults of the same
    name.  Unknown names are added so operators can register new
    presets from disk without code changes.
    """
    for name, profile in loaded.items():
        CRITERION_PROFILE_REGISTRY[name] = profile


# ---------------------------------------------------------------------------
# Task-side helpers
# ---------------------------------------------------------------------------


def extract_from_task(task: Any) -> CriterionProfile | None:
    """Return the resolved profile for *task* or ``None`` when absent.

    The function honours :func:`is_enabled` first; when the feature
    flag is off it always returns ``None`` regardless of task content.

    The lookup is fault-tolerant: tasks without ``metadata``, without
    the ``criterion_profile`` key, or with a malformed value all
    yield ``None`` with a warning rather than raising.  Strict callers
    should call :func:`resolve` directly.
    """
    if not is_enabled():
        return None
    metadata: object = getattr(task, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    metadata_dict = cast("dict[str, Any]", metadata)
    spec: object = metadata_dict.get("criterion_profile")
    if spec is None:
        return None
    try:
        return resolve(spec)
    except CriterionProfileError as exc:
        logger.warning(
            "Task %s carries invalid criterion_profile %r: %s",
            getattr(task, "id", "<unknown>"),
            spec,
            exc,
        )
        return None


def inherit_for_child(
    parent_metadata: Mapping[str, Any] | None,
    child_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a child metadata dict that inherits the parent's profile.

    The child's explicit ``criterion_profile`` (if any) wins; otherwise
    the parent's value is copied in.  Returns a new dict - neither
    input is mutated.
    """
    base: dict[str, Any] = dict(child_metadata or {})
    if "criterion_profile" in base:
        return base
    if parent_metadata is None:
        return base
    parent_spec = parent_metadata.get("criterion_profile")
    if parent_spec is None:
        return base
    base["criterion_profile"] = parent_spec
    return base


def describe(profile: CriterionProfile) -> str:
    """Return a short human-readable description for the show command."""
    weights = profile.as_dict()
    body = ", ".join(f"{axis}={weights[axis]:.3f}" for axis in AXES)
    return f"preset={profile.name} {body}"


# ---------------------------------------------------------------------------
# Router bias
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingBias:
    """Hard-bias hint emitted by the router preselection pass.

    The router reads this once per task.  When ``forced_model`` is
    set the bandit/cascade routers are skipped entirely; otherwise the
    hints below merely tighten the candidate set or the effort tier.

    Attributes:
        forced_model: Pin the task to a specific model id (e.g. ``"opus"``).
            ``None`` means "no hard pin".
        forced_effort: Pin the task to a specific effort level (e.g.
            ``"max"`` for opus or ``"low"`` for haiku).  ``None`` means
            "use the model's default effort".
        max_blast_radius: Override the per-task blast-radius cap when
            set.  ``None`` means "use the orchestrator default".
        rationale: Human-readable reason for the bias.  Always
            non-empty, surfaced into the routing decision log.
    """

    forced_model: str | None
    forced_effort: str | None
    max_blast_radius: int | None
    rationale: str


def derive_bias(profile: CriterionProfile) -> RoutingBias:
    """Translate a :class:`CriterionProfile` into a :class:`RoutingBias`.

    The mapping is intentionally deterministic so that two runs with
    the same profile vector always produce the same bias - replay
    invariants depend on it.

    Resolution order (first match wins):

    1. ``reversibility`` dominant -> opus/max + blast radius 1.
    2. ``correctness`` dominant -> opus/max.
    3. ``cost`` dominant -> haiku/low.
    4. ``latency`` dominant -> haiku/low.
    5. Otherwise -> sonnet/high.
    """
    profile.validate()

    if profile.is_reversibility_dominant():
        return RoutingBias(
            forced_model="opus",
            forced_effort="max",
            max_blast_radius=1,
            rationale=(f"reversibility={profile.reversibility:.2f} dominant - pin to opus/max with tight blast radius"),
        )

    if profile.is_correctness_dominant():
        return RoutingBias(
            forced_model="opus",
            forced_effort="max",
            max_blast_radius=None,
            rationale=(f"correctness={profile.correctness:.2f} dominant - pin to opus/max"),
        )

    if profile.is_cost_dominant():
        return RoutingBias(
            forced_model="haiku",
            forced_effort="low",
            max_blast_radius=None,
            rationale=(f"cost={profile.cost:.2f} dominant - pin to haiku/low"),
        )

    if profile.is_latency_dominant():
        return RoutingBias(
            forced_model="haiku",
            forced_effort="low",
            max_blast_radius=None,
            rationale=(f"latency={profile.latency:.2f} dominant - pin to haiku/low for fast turnaround"),
        )

    return RoutingBias(
        forced_model="sonnet",
        forced_effort="high",
        max_blast_radius=None,
        rationale=(f"no dominant axis - default to sonnet/high (vector={profile.as_vector()})"),
    )


def replace_in_registry(name: str, **changes: Any) -> CriterionProfile:
    """Replace fields on the registry entry *name* and return the new instance."""
    if name not in CRITERION_PROFILE_REGISTRY:
        raise KeyError(name)
    current = CRITERION_PROFILE_REGISTRY[name]
    updated = _typed_replace(current, **changes)
    updated.validate()
    CRITERION_PROFILE_REGISTRY[name] = updated
    return updated


__all__ = [
    "AXES",
    "CRITERION_PROFILE_REGISTRY",
    "ENV_FLAG",
    "INLINE_PRESET_NAME",
    "SUM_TOLERANCE",
    "CriterionProfile",
    "CriterionProfileError",
    "RoutingBias",
    "derive_bias",
    "describe",
    "extract_from_task",
    "from_dict",
    "inherit_for_child",
    "install_loaded_profiles",
    "is_enabled",
    "load_profiles_from_dir",
    "normalize",
    "replace_in_registry",
    "resolve",
]
