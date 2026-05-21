"""Immutable feature-list contract with per-feature pass/fail tracking.

A *feature contract* is a list of operator-authored features that an
implementation must deliver before a task may be marked complete. The
contract is persisted as JSON at ``.sdd/contract/features.json`` and
hash-anchored against the audit-log chain (see
:mod:`bernstein.core.security.audit`) so that any in-place edit by an agent
is detectable.

The on-disk JSON is intentionally simple and forwards-compatible:

.. code-block:: json

    {
      "schema_version": 1,
      "anchor": "<sha256-hex of canonical features list>",
      "created_at": "2026-05-05T00:00:00Z",
      "features": [
        {
          "id": "F-001",
          "category": "api",
          "description": "POST /tasks creates a task",
          "acceptance_steps": ["call API", "assert 201"],
          "acceptance_check": "pytest tests/test_tasks.py::test_create",
          "passes": false,
          "evidence_path": null
        }
      ]
    }

Future consumers (notably the spec-as-test-loop ticket) read the same file
and treat ``schema_version`` as a hard compatibility gate; new fields are
added with safe defaults rather than by bumping the schema.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from bernstein.core.security.audit import AuditLog

SCHEMA_VERSION = 1

DEFAULT_CONTRACT_PATH = Path(".sdd/contract/features.json")

_FEATURE_FIELDS: tuple[str, ...] = (
    "id",
    "category",
    "description",
    "acceptance_steps",
    "acceptance_check",
    "passes",
    "evidence_path",
)


class FeatureContractError(RuntimeError):
    """Base error for feature-contract violations."""


class TamperingDetectedError(FeatureContractError):
    """Raised when the on-disk anchor does not match the recomputed digest."""


class UnknownFeatureError(FeatureContractError):
    """Raised when an operation references a feature id not in the contract."""


class SchemaVersionError(FeatureContractError):
    """Raised when the on-disk schema_version is incompatible with this code."""


@dataclass(frozen=True)
class Feature:
    """A single immutable feature entry inside a contract.

    Attributes:
        id: Stable, operator-assigned identifier (e.g. ``F-001``).
        category: Free-form bucket used only for grouping in CLI output.
        description: One-line human summary of what the feature delivers.
        acceptance_steps: Ordered, human-readable steps an operator could
            run to convince themselves the feature works.
        acceptance_check: Machine-executable command (shell or pytest
            selector) whose exit code decides ``passes``.
        passes: True iff the most recent run of ``acceptance_check``
            succeeded. Defaults to False - a feature is pending until proven.
        evidence_path: Optional path to a log/screenshot/output artefact
            captured the last time the check was run.
    """

    id: str
    category: str
    description: str
    acceptance_steps: list[str] = field(default_factory=list[str])
    acceptance_check: str = ""
    passes: bool = False
    evidence_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict with stable key ordering."""
        return {k: getattr(self, k) for k in _FEATURE_FIELDS}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Feature:
        """Build a Feature from its JSON dict, ignoring unknown fields.

        Unknown fields are dropped silently so that a future schema_version
        adding new optional fields can still be read by older code paths
        that only need the v1 surface.
        """
        return cls(
            id=str(data["id"]),
            category=str(data.get("category", "")),
            description=str(data.get("description", "")),
            acceptance_steps=list(data.get("acceptance_steps", []) or []),
            acceptance_check=str(data.get("acceptance_check", "")),
            passes=bool(data.get("passes", False)),
            evidence_path=(None if data.get("evidence_path") in (None, "") else str(data["evidence_path"])),
        )


def _canonical_payload(features: Iterable[Feature]) -> bytes:
    """Return the canonical bytes used to compute the integrity anchor.

    The anchor only covers the *immutable* shape of the contract - the set
    of feature ids, their descriptions, acceptance steps and acceptance
    checks. Mutable fields (``passes``, ``evidence_path``) are excluded so
    that legitimately flipping a feature to passing does not invalidate the
    anchor.
    """
    frozen = [
        {
            "id": f.id,
            "category": f.category,
            "description": f.description,
            "acceptance_steps": f.acceptance_steps.copy(),
            "acceptance_check": f.acceptance_check,
        }
        for f in features
    ]
    return json.dumps(
        {"schema_version": SCHEMA_VERSION, "features": frozen},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def compute_anchor(features: Iterable[Feature]) -> str:
    """Return the SHA-256 hex digest of the canonical contract payload."""
    return hashlib.sha256(_canonical_payload(features)).hexdigest()


@dataclass
class FeatureContract:
    """An immutable list of features with per-entry pass/fail tracking.

    The set of features (id, category, description, acceptance_steps,
    acceptance_check) is locked at creation time. Only ``passes`` and
    ``evidence_path`` may be mutated, and only via :meth:`mark_pass` /
    :meth:`mark_fail` so that the anchor stays valid.
    """

    features: list[Feature]
    anchor: str
    created_at: str
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(cls, features: Iterable[Feature]) -> FeatureContract:
        """Build a new contract from an iterable of Feature entries.

        Raises:
            FeatureContractError: If feature ids are not unique.
        """
        features_list = list(features)
        ids = [f.id for f in features_list]
        if len(set(ids)) != len(ids):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise FeatureContractError(f"duplicate feature ids: {duplicates}")
        return cls(
            features=features_list,
            anchor=compute_anchor(features_list),
            created_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )

    # -- IO -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form written to disk."""
        return {
            "schema_version": self.schema_version,
            "anchor": self.anchor,
            "created_at": self.created_at,
            "features": [f.to_dict() for f in self.features],
        }

    def save(self, path: Path = DEFAULT_CONTRACT_PATH) -> Path:
        """Atomically write the contract to ``path`` and return it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path = DEFAULT_CONTRACT_PATH) -> FeatureContract:
        """Load and integrity-check a contract from ``path``.

        Raises:
            FileNotFoundError: If the contract file does not exist.
            SchemaVersionError: If the on-disk schema_version is unknown.
            TamperingDetectedError: If the recomputed anchor does not match
                the stored anchor.
        """
        raw = json.loads(path.read_text())
        version = int(raw.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(f"unsupported schema_version {version}; expected {SCHEMA_VERSION}")
        features = [Feature.from_dict(d) for d in raw.get("features", [])]
        stored_anchor = str(raw.get("anchor", ""))
        recomputed = compute_anchor(features)
        if stored_anchor != recomputed:
            raise TamperingDetectedError(
                f"feature contract anchor mismatch at {path}: "
                f"stored={stored_anchor[:16]}… recomputed={recomputed[:16]}…"
            )
        return cls(
            features=features,
            anchor=stored_anchor,
            created_at=str(raw.get("created_at", "")),
            schema_version=version,
        )

    # -- mutation -----------------------------------------------------------

    def _replace_feature(self, feature_id: str, **changes: Any) -> None:
        for idx, feat in enumerate(self.features):
            if feat.id == feature_id:
                self.features[idx] = replace(feat, **changes)
                return
        raise UnknownFeatureError(f"unknown feature id: {feature_id!r}")

    def mark_pass(self, feature_id: str, evidence_path: str | None = None) -> None:
        """Flip ``passes`` to True for ``feature_id``.

        The anchor is unchanged because the canonical payload excludes
        mutable fields.
        """
        self._replace_feature(feature_id, passes=True, evidence_path=evidence_path)

    def mark_fail(self, feature_id: str, evidence_path: str | None = None) -> None:
        """Flip ``passes`` to False for ``feature_id``."""
        self._replace_feature(feature_id, passes=False, evidence_path=evidence_path)

    # -- queries ------------------------------------------------------------

    def pending(self) -> list[Feature]:
        """Return features whose ``passes`` is still False."""
        return [f for f in self.features if not f.passes]

    def all_pass(self) -> bool:
        """Return True iff every feature has ``passes`` set to True."""
        return all(f.passes for f in self.features)

    def by_id(self, feature_id: str) -> Feature:
        """Return the feature with ``feature_id`` or raise."""
        for feat in self.features:
            if feat.id == feature_id:
                return feat
        raise UnknownFeatureError(f"unknown feature id: {feature_id!r}")

    # -- audit anchoring ----------------------------------------------------

    def record_anchor(self, audit_log: AuditLog, actor: str = "system") -> None:
        """Write the contract anchor into the HMAC-chained audit log.

        The audit chain provides the second leg of tamper-evidence: even if
        an attacker rewrites both ``features.json`` and recomputes the
        embedded ``anchor``, the audit log entry pins the original anchor
        to a moment in HMAC-chain time.
        """
        audit_log.log(
            event_type="feature_contract.anchor",
            actor=actor,
            resource_type="feature_contract",
            resource_id=self.anchor,
            details={
                "schema_version": self.schema_version,
                "feature_count": len(self.features),
                "feature_ids": [f.id for f in self.features],
                "created_at": self.created_at,
            },
        )


def features_from_plan_step(step: dict[str, Any]) -> list[Feature]:
    """Extract a list of Features from a plan step's optional ``features`` key.

    Returns an empty list when the step has no ``features`` entry, which
    preserves back-compat with featureless plans.
    """
    raw: object = step.get("features") or []
    if not isinstance(raw, list):
        raise FeatureContractError(f"plan step 'features' must be a list, got {type(raw).__name__}")
    items: list[Any] = raw  # pyright: ignore[reportUnknownVariableType]
    return [Feature.from_dict(_normalise_feature_dict(item)) for item in items]


def _normalise_feature_dict(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise FeatureContractError(f"feature entries must be objects, got {type(item).__name__}")
    raw_items: list[tuple[Any, Any]] = list(item.items())  # pyright: ignore[reportUnknownArgumentType]
    typed: dict[str, Any] = {str(k): v for k, v in raw_items}
    if "id" not in typed:
        raise FeatureContractError("feature entry missing required field 'id'")
    return typed


__all__ = [
    "DEFAULT_CONTRACT_PATH",
    "SCHEMA_VERSION",
    "Feature",
    "FeatureContract",
    "FeatureContractError",
    "SchemaVersionError",
    "TamperingDetectedError",
    "UnknownFeatureError",
    "compute_anchor",
    "features_from_plan_step",
]
