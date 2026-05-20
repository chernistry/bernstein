"""Strict structured-output validation and user-owned-field protection.

Adapters parse model-emitted structured output and merge it into stored
artefacts. Two failure modes follow when that parse is lenient:

* Hallucinated keys land in storage and break downstream consumers that
  did not expect them. The defence is to reject extra keys at parse time
  (``additionalProperties: false`` for JSON Schema, ``extra="forbid"``
  for Pydantic).
* AI updates overwrite operator-owned fields (notes, tags, ``id``). The
  defence is an explicit blacklist of user-owned fields that are stripped
  from any incoming AI update before merge, never trusted from the model.

This module provides the shared primitives both defences need so each
adapter and schema does not reinvent them:

* :func:`seal_schema` and :func:`assert_schema_sealed` make a JSON Schema
  strict (no additional properties) and assert that it is.
* :class:`UserOwnedFieldRegistry` maps a schema id to the set of fields
  that the model must never write.
* :func:`strip_user_owned_fields` removes blacklisted keys from an
  incoming AI update and logs an ``AIWriteRejected`` event naming the
  schema and the rejected fields.
* :class:`SchemaViolation` and :func:`classify_schema_violation` let the
  API-level error path mark ``additional_properties`` rejections as a
  bounded, retryable schema fault rather than an opaque error.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "USER_OWNED_FIELDS",
    "AIWriteRejected",
    "SchemaViolation",
    "UserOwnedFieldRegistry",
    "assert_schema_sealed",
    "classify_schema_violation",
    "default_user_owned_registry",
    "seal_schema",
    "strip_user_owned_fields",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User-owned field blacklist
# ---------------------------------------------------------------------------


USER_OWNED_FIELDS: frozenset[str] = frozenset(
    {
        "user_notes",
        "operator_overrides",
        "id",
        "created_at",
    }
)
"""Default set of operator-owned fields the model must never write.

These fields are managed by operators or by the store, not by AI output.
An AI update that touches any of them is stripped before merge. Schemas
may extend this set via :class:`UserOwnedFieldRegistry`.
"""


@dataclass(frozen=True, slots=True)
class AIWriteRejected:
    """Audit record for a rejected AI write of an operator-owned field.

    Emitted whenever :func:`strip_user_owned_fields` removes one or more
    blacklisted keys from an incoming AI update. The record names the
    schema and the rejected fields so the audit surface can attribute the
    attempt without inspecting the raw payload.

    Attributes:
        schema: Stable schema id (or logical name) the update targeted.
        fields: Sorted tuple of the rejected field names.
    """

    schema: str
    fields: tuple[str, ...]

    def __str__(self) -> str:
        joined = ", ".join(self.fields)
        return f"AIWriteRejected{{schema={self.schema!r}, fields=[{joined}]}}"


class UserOwnedFieldRegistry:
    """Per-schema registry of operator-owned (AI-write-forbidden) fields.

    Each schema id maps to the set of fields the model must never write.
    The default set (:data:`USER_OWNED_FIELDS`) applies to every schema;
    per-schema entries add to it rather than replacing it, so the global
    floor is never weakened by a per-schema registration.
    """

    def __init__(self, default_fields: Iterable[str] = USER_OWNED_FIELDS) -> None:
        self._default: frozenset[str] = frozenset(default_fields)
        self._by_schema: dict[str, frozenset[str]] = {}

    def register(self, schema_id: str, fields: Iterable[str]) -> None:
        """Register additional user-owned *fields* for *schema_id*.

        The registered fields are unioned with the global default set;
        re-registering a schema id replaces only its per-schema extras.
        """
        self._by_schema[schema_id] = frozenset(fields)

    def fields_for(self, schema_id: str) -> frozenset[str]:
        """Return the full user-owned field set for *schema_id*.

        The global default is always included so an unregistered schema id
        still protects the floor fields.
        """
        return self._default | self._by_schema.get(schema_id, frozenset())


_DEFAULT_REGISTRY = UserOwnedFieldRegistry()


def default_user_owned_registry() -> UserOwnedFieldRegistry:
    """Return the process-wide default :class:`UserOwnedFieldRegistry`."""
    return _DEFAULT_REGISTRY


def strip_user_owned_fields(
    schema_id: str,
    update: Mapping[str, Any],
    *,
    registry: UserOwnedFieldRegistry | None = None,
) -> tuple[dict[str, Any], AIWriteRejected | None]:
    """Strip operator-owned fields from an incoming AI *update*.

    Any key in the schema's user-owned field set is removed from a copy of
    *update* before the merge layer sees it. When at least one field is
    stripped, an ``AIWriteRejected`` event is logged and returned so the
    caller can surface it on the audit trail.

    Args:
        schema_id: Stable schema id (or logical name) the update targets.
        update: Decoded AI update mapping. Not mutated.
        registry: Override registry; defaults to the process-wide one.

    Returns:
        A ``(safe_update, rejected)`` pair. ``safe_update`` is a new dict
        with blacklisted keys removed. ``rejected`` is ``None`` when no
        field was stripped, else the :class:`AIWriteRejected` record.
    """
    reg = registry or _DEFAULT_REGISTRY
    owned = reg.fields_for(schema_id)
    safe: dict[str, Any] = {}
    rejected_fields: list[str] = []
    for key, value in update.items():
        if key in owned:
            rejected_fields.append(key)
            continue
        safe[key] = value
    if not rejected_fields:
        return safe, None
    record = AIWriteRejected(schema=schema_id, fields=tuple(sorted(rejected_fields)))
    logger.warning(
        "AIWriteRejected schema=%s rejected operator-owned fields=%s",
        schema_id,
        ", ".join(record.fields),
        extra={"schema": schema_id, "rejected_fields": list(record.fields)},
    )
    return safe, record


# ---------------------------------------------------------------------------
# JSON Schema sealing (additionalProperties: false)
# ---------------------------------------------------------------------------


def seal_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *schema* with object nodes sealed against extras.

    Recursively sets ``additionalProperties: false`` on every object node
    that declares ``properties``. Nodes that already pin
    ``additionalProperties`` (to any value) are left untouched so a schema
    that deliberately opens a sub-object is not silently re-sealed.

    Args:
        schema: A JSON Schema mapping. Not mutated.

    Returns:
        A new schema dict with object nodes sealed.
    """

    def _seal(node: object) -> object:
        if isinstance(node, dict):
            mapping = cast("dict[str, Any]", node)
            sealed: dict[str, Any] = {key: _seal(value) for key, value in mapping.items()}
            if "properties" in sealed and "additionalProperties" not in sealed:
                sealed["additionalProperties"] = False
            return sealed
        if isinstance(node, list):
            items = cast("list[Any]", node)
            return [_seal(item) for item in items]
        return node

    return cast("dict[str, Any]", _seal(schema))


def assert_schema_sealed(schema: Mapping[str, Any]) -> None:
    """Raise :class:`ValueError` if any object node permits extra keys.

    Walks the schema and fails on the first object node that declares
    ``properties`` without ``additionalProperties: false``. Use this in
    tests and at registration time to prove a structured-output schema
    cannot accept hallucinated keys.

    Args:
        schema: A JSON Schema mapping.

    Raises:
        ValueError: If any object node leaves additional properties open.
    """

    def _walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            mapping = cast("dict[str, Any]", node)
            if "properties" in mapping and mapping.get("additionalProperties") is not False:
                location = path or "<root>"
                msg = f"object node at {location} does not seal additionalProperties"
                raise ValueError(msg)
            for key, value in mapping.items():
                _walk(value, f"{path}/{key}" if path else key)
        elif isinstance(node, list):
            items = cast("list[Any]", node)
            for index, item in enumerate(items):
                _walk(item, f"{path}/{index}")

    _walk(dict(schema), "")


# ---------------------------------------------------------------------------
# Provider error classification
# ---------------------------------------------------------------------------


class SchemaViolation(ValueError):
    """A structured-output response violated its strict schema.

    Carries the offending fields (when known) so the retry path can bound
    its attempts: a schema violation is deterministic, so an unbounded
    retry would loop. Callers should treat this as a non-transient fault.

    Attributes:
        fields: Field names the provider flagged as additional / invalid.
            Empty when the provider did not name them.
    """

    def __init__(self, message: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.fields = fields


# Substrings that providers and the local validator use to flag a payload
# carrying keys the schema did not declare. Compared case-insensitively.
_ADDITIONAL_PROPERTY_MARKERS: tuple[str, ...] = (
    "additionalproperties",
    "additional properties",
    "additional_properties",
    "extra fields not permitted",
    "unexpected keyword argument",
    "is not allowed",
)

_FIELD_NAME_RE = re.compile(r"'([^']+)'")


def classify_schema_violation(error: Exception | str) -> SchemaViolation | None:
    """Classify *error* as a strict-schema ``additional_properties`` fault.

    Inspects the rendered error message for the markers providers use when
    a structured-output payload carried undeclared keys. When matched,
    returns a :class:`SchemaViolation` (with any quoted field names lifted
    from the message); otherwise returns ``None`` so the caller can fall
    back to its general error path.

    Args:
        error: The raised exception or a pre-rendered provider message.

    Returns:
        A :class:`SchemaViolation` when the error is an additional-property
        rejection, else ``None``.
    """
    message = str(error)
    lowered = message.lower()
    if not any(marker in lowered for marker in _ADDITIONAL_PROPERTY_MARKERS):
        return None
    fields = tuple(_FIELD_NAME_RE.findall(message))
    return SchemaViolation(
        f"structured output rejected for additional properties: {message}",
        fields=fields,
    )
