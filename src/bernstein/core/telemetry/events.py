"""Event schema and serializer for opt-in operator observability.

This module defines the closed taxonomy of telemetry events emitted by
Bernstein when an operator has explicitly opted in.  No event variant
outside the enumerations below is permitted, and every event payload is
strictly validated before serialization.

The event set is intentionally minimal:

* ``install_completed``    - emitted once after a fresh install
* ``first_run_started``    - the operator started the first ``bernstein`` run
* ``first_run_completed``  - the first run finished (ok or with a category)
* ``command_invoked``      - a worker subprocess was spawned (name only)
* ``daily_active``         - one heartbeat per UTC day per install id

Error categories are likewise a closed set:
``config_missing | auth_failed | dependency_missing | model_unreachable |
timeout | unknown``.

No event payload may contain free-form text, file contents, prompts,
resource identifiers, args, env vars, or anything resembling secrets.
The serializer enforces this by accepting only the typed fields below.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final


class TelemetryEvent(StrEnum):
    """Closed set of event names that may appear in the payload."""

    INSTALL_COMPLETED = "install_completed"
    FIRST_RUN_STARTED = "first_run_started"
    FIRST_RUN_COMPLETED = "first_run_completed"
    COMMAND_INVOKED = "command_invoked"
    DAILY_ACTIVE = "daily_active"


class ErrorCategory(StrEnum):
    """Closed set of error categories for first_run_completed events."""

    CONFIG_MISSING = "config_missing"
    AUTH_FAILED = "auth_failed"
    DEPENDENCY_MISSING = "dependency_missing"
    MODEL_UNREACHABLE = "model_unreachable"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


SCHEMA_VERSION: Final[int] = 1


# ---------------------------------------------------------------------------
# Event payload dataclasses (strictly typed, closed field sets)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstallCompletedPayload:
    """Fields emitted with install_completed."""

    os: str
    py_version: str
    install_method: str
    bernstein_version: str


@dataclass(frozen=True, slots=True)
class FirstRunStartedPayload:
    """Fields emitted with first_run_started."""

    time_since_install_seconds: int


@dataclass(frozen=True, slots=True)
class FirstRunCompletedPayload:
    """Fields emitted with first_run_completed."""

    ok: bool
    duration_ms: int
    error_category: ErrorCategory | None = None


@dataclass(frozen=True, slots=True)
class CommandInvokedPayload:
    """Fields emitted with command_invoked.  Only the name and version."""

    name_only: str
    bernstein_version: str


@dataclass(frozen=True, slots=True)
class DailyActivePayload:
    """Fields emitted with daily_active.  ISO-8601 UTC date only."""

    day_iso: str


EventPayload = (
    InstallCompletedPayload
    | FirstRunStartedPayload
    | FirstRunCompletedPayload
    | CommandInvokedPayload
    | DailyActivePayload
)


# ---------------------------------------------------------------------------
# Envelope + serializer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Wire envelope.  ``install_id`` is required for every emitted event."""

    name: TelemetryEvent
    install_id: str
    payload: EventPayload
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    schema_version: int = SCHEMA_VERSION


_PAYLOAD_FOR: Final[dict[TelemetryEvent, type[EventPayload]]] = {
    TelemetryEvent.INSTALL_COMPLETED: InstallCompletedPayload,
    TelemetryEvent.FIRST_RUN_STARTED: FirstRunStartedPayload,
    TelemetryEvent.FIRST_RUN_COMPLETED: FirstRunCompletedPayload,
    TelemetryEvent.COMMAND_INVOKED: CommandInvokedPayload,
    TelemetryEvent.DAILY_ACTIVE: DailyActivePayload,
}


def expected_payload_type(name: TelemetryEvent) -> type[EventPayload]:
    """Return the dataclass that ``name`` requires.

    Useful for tests and the consistency check inside ``serialize_event``.
    """
    return _PAYLOAD_FOR[name]


def _payload_to_dict(payload: EventPayload) -> dict[str, Any]:
    """Convert a payload dataclass to a JSON-serializable dict.

    Enum values are reduced to their string form.  ``None`` fields are
    elided so receivers can rely on field presence to detect optionality.
    """
    out: dict[str, Any] = {}
    for slot in payload.__slots__:  # pyright: ignore[reportAny]
        value = getattr(payload, slot)
        if value is None:
            continue
        if isinstance(value, StrEnum):
            out[slot] = value.value
        else:
            out[slot] = value
    return out


def serialize_event(envelope: EventEnvelope) -> str:
    """Serialize an envelope to a single JSON line.

    Raises:
        ValueError: if the envelope's payload type does not match its name,
            or if the install id is empty.
    """
    if not envelope.install_id:
        raise ValueError("install_id is required for every emitted event")
    expected = _PAYLOAD_FOR[envelope.name]
    if not isinstance(envelope.payload, expected):
        raise ValueError(
            f"payload type {type(envelope.payload).__name__} does not match "
            f"event {envelope.name.value} (expected {expected.__name__})"
        )
    body: dict[str, Any] = {
        "schema_version": envelope.schema_version,
        "name": envelope.name.value,
        "install_id": envelope.install_id,
        "timestamp": envelope.timestamp,
        "payload": _payload_to_dict(envelope.payload),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def build_envelope(
    name: TelemetryEvent,
    install_id: str,
    payload: EventPayload,
    *,
    timestamp: str | None = None,
) -> EventEnvelope:
    """Construct an envelope, optionally with a fixed timestamp for tests."""
    if timestamp is None:
        return EventEnvelope(name=name, install_id=install_id, payload=payload)
    return EventEnvelope(
        name=name,
        install_id=install_id,
        payload=payload,
        timestamp=timestamp,
    )


__all__ = [
    "SCHEMA_VERSION",
    "CommandInvokedPayload",
    "DailyActivePayload",
    "ErrorCategory",
    "EventEnvelope",
    "EventPayload",
    "FirstRunCompletedPayload",
    "FirstRunStartedPayload",
    "InstallCompletedPayload",
    "TelemetryEvent",
    "build_envelope",
    "expected_payload_type",
    "serialize_event",
]
