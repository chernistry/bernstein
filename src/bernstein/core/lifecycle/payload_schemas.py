"""Payload schemas for the standardised cross-CLI lifecycle events (T1323).

Each entry in :data:`PAYLOAD_SCHEMAS` declares the required and optional
keys for ``LifecycleContext.data`` when firing a given event. Schemas
are intentionally permissive: extra keys are allowed so plugins can
attach annotations, but missing required keys raise
:class:`PayloadSchemaError` early so a hook never receives an
ambiguous payload.

The schemas mirror the contract documented in
``docs/contributing/hooks.md`` - keep both in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bernstein.core.lifecycle.hooks import LifecycleEvent

__all__ = [
    "PAYLOAD_SCHEMAS",
    "PayloadSchema",
    "PayloadSchemaError",
    "validate_payload",
]


class PayloadSchemaError(ValueError):
    """Raised when a payload does not satisfy its event schema."""


@dataclass(frozen=True, slots=True)
class PayloadSchema:
    """Declarative schema for a lifecycle event payload.

    Attributes:
        required: Keys that must be present in ``LifecycleContext.data``.
        optional: Keys that are documented but not required. Extra keys
            outside both lists are allowed (forward-compatible).
    """

    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


PAYLOAD_SCHEMAS: dict[LifecycleEvent, PayloadSchema] = {
    LifecycleEvent.SESSION_START: PayloadSchema(
        required=("session_id",),
        optional=("role", "prompt_template_sha", "env_snapshot"),
    ),
    LifecycleEvent.USER_PROMPT_SUBMITTED: PayloadSchema(
        required=("session_id", "prompt"),
        optional=("attached_files",),
    ),
    LifecycleEvent.PRE_TOOL_USE: PayloadSchema(
        required=("session_id", "tool", "args"),
        optional=("blast_radius_score",),
    ),
    LifecycleEvent.POST_TOOL_USE: PayloadSchema(
        required=("session_id", "tool", "args", "result"),
        optional=("duration_ms", "cost", "success"),
    ),
    LifecycleEvent.ERROR_OCCURRED: PayloadSchema(
        required=("session_id", "error_class", "message"),
        optional=("recovery_path",),
    ),
    LifecycleEvent.IDLE: PayloadSchema(
        required=("session_id", "idle_duration_s"),
    ),
    LifecycleEvent.SESSION_END: PayloadSchema(
        required=("session_id", "status"),
        optional=("total_cost", "total_tokens"),
    ),
}


def validate_payload(event: LifecycleEvent, data: dict[str, Any]) -> None:
    """Validate ``data`` against the schema declared for ``event``.

    Events without an explicit schema (the pre-existing snake_case
    family) accept any payload.

    Args:
        event: The lifecycle event the payload belongs to.
        data: The payload mapping to validate.

    Raises:
        PayloadSchemaError: If a required key is missing.
    """
    schema = PAYLOAD_SCHEMAS.get(event)
    if schema is None:
        return
    missing = [key for key in schema.required if key not in data]
    if missing:
        raise PayloadSchemaError(
            f"payload for '{event.value}' missing required keys: {sorted(missing)}",
        )
