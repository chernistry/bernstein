"""Event schema and serializer tests for every event variant."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bernstein.core.telemetry import (
    SCHEMA_VERSION,
    CommandInvokedPayload,
    DailyActivePayload,
    ErrorCategory,
    EventEnvelope,
    FirstRunCompletedPayload,
    FirstRunStartedPayload,
    InstallCompletedPayload,
    TelemetryEvent,
    build_envelope,
    serialize_event,
)
from bernstein.core.telemetry.events import expected_payload_type

# ---------------------------------------------------------------------------
# Enum integrity (closed set)
# ---------------------------------------------------------------------------


def test_event_names_are_exhaustive() -> None:
    expected = {
        "install_completed",
        "first_run_started",
        "first_run_completed",
        "command_invoked",
        "daily_active",
    }
    assert {e.value for e in TelemetryEvent} == expected


def test_error_categories_are_exhaustive() -> None:
    expected = {
        "config_missing",
        "auth_failed",
        "dependency_missing",
        "model_unreachable",
        "timeout",
        "unknown",
    }
    assert {e.value for e in ErrorCategory} == expected


# ---------------------------------------------------------------------------
# Per-variant serializer happy paths
# ---------------------------------------------------------------------------


def _decode(envelope: EventEnvelope) -> dict[str, Any]:
    return json.loads(serialize_event(envelope))


def test_install_completed_round_trip() -> None:
    env = build_envelope(
        TelemetryEvent.INSTALL_COMPLETED,
        install_id="abc",
        payload=InstallCompletedPayload(
            os="linux",
            py_version="3.12.0",
            install_method="pip",
            bernstein_version="2.0.1",
        ),
    )
    body = _decode(env)
    assert body["name"] == "install_completed"
    assert body["install_id"] == "abc"
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["payload"] == {
        "os": "linux",
        "py_version": "3.12.0",
        "install_method": "pip",
        "bernstein_version": "2.0.1",
    }


def test_first_run_started_round_trip() -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_STARTED,
        install_id="abc",
        payload=FirstRunStartedPayload(time_since_install_seconds=42),
    )
    body = _decode(env)
    assert body["payload"] == {"time_since_install_seconds": 42}


def test_first_run_completed_ok_round_trip() -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_COMPLETED,
        install_id="abc",
        payload=FirstRunCompletedPayload(ok=True, duration_ms=123),
    )
    body = _decode(env)
    assert body["payload"] == {"ok": True, "duration_ms": 123}


def test_first_run_completed_error_round_trip() -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_COMPLETED,
        install_id="abc",
        payload=FirstRunCompletedPayload(
            ok=False,
            duration_ms=999,
            error_category=ErrorCategory.AUTH_FAILED,
        ),
    )
    body = _decode(env)
    assert body["payload"] == {
        "ok": False,
        "duration_ms": 999,
        "error_category": "auth_failed",
    }


def test_command_invoked_round_trip() -> None:
    env = build_envelope(
        TelemetryEvent.COMMAND_INVOKED,
        install_id="abc",
        payload=CommandInvokedPayload(name_only="claude", bernstein_version="2.0.1"),
    )
    body = _decode(env)
    assert body["payload"] == {"name_only": "claude", "bernstein_version": "2.0.1"}


def test_daily_active_round_trip() -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="abc",
        payload=DailyActivePayload(day_iso="2026-05-17"),
    )
    body = _decode(env)
    assert body["payload"] == {"day_iso": "2026-05-17"}


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_serializer_rejects_empty_install_id() -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="",
        payload=DailyActivePayload(day_iso="2026-05-17"),
    )
    with pytest.raises(ValueError, match="install_id"):
        serialize_event(env)


def test_serializer_rejects_mismatched_payload() -> None:
    env = EventEnvelope(
        name=TelemetryEvent.DAILY_ACTIVE,
        install_id="abc",
        payload=CommandInvokedPayload(name_only="x", bernstein_version="2.0.1"),
    )
    with pytest.raises(ValueError, match="does not match"):
        serialize_event(env)


def test_expected_payload_type_mapping() -> None:
    assert expected_payload_type(TelemetryEvent.INSTALL_COMPLETED) is InstallCompletedPayload
    assert expected_payload_type(TelemetryEvent.FIRST_RUN_STARTED) is FirstRunStartedPayload
    assert expected_payload_type(TelemetryEvent.FIRST_RUN_COMPLETED) is FirstRunCompletedPayload
    assert expected_payload_type(TelemetryEvent.COMMAND_INVOKED) is CommandInvokedPayload
    assert expected_payload_type(TelemetryEvent.DAILY_ACTIVE) is DailyActivePayload


# ---------------------------------------------------------------------------
# JSON shape invariants
# ---------------------------------------------------------------------------


def test_serializer_output_is_one_line() -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="abc",
        payload=DailyActivePayload(day_iso="2026-05-17"),
    )
    line = serialize_event(env)
    assert "\n" not in line


def test_serializer_output_keys_sorted() -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="abc",
        payload=DailyActivePayload(day_iso="2026-05-17"),
    )
    line = serialize_event(env)
    # Top-level keys are sorted alphabetically.
    body = json.loads(line)
    assert list(body.keys()) == sorted(body.keys())


def test_serializer_drops_none_optional_fields() -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_COMPLETED,
        install_id="abc",
        payload=FirstRunCompletedPayload(ok=True, duration_ms=10),
    )
    body = _decode(env)
    assert "error_category" not in body["payload"]


def test_serializer_uses_str_enum_values() -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_COMPLETED,
        install_id="abc",
        payload=FirstRunCompletedPayload(ok=False, duration_ms=10, error_category=ErrorCategory.TIMEOUT),
    )
    body = _decode(env)
    assert body["payload"]["error_category"] == "timeout"
    assert isinstance(body["payload"]["error_category"], str)


def test_build_envelope_accepts_explicit_timestamp() -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="abc",
        payload=DailyActivePayload(day_iso="2026-05-17"),
        timestamp="2026-05-17T00:00:00+00:00",
    )
    body = _decode(env)
    assert body["timestamp"] == "2026-05-17T00:00:00+00:00"


def test_build_envelope_default_timestamp_iso() -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="abc",
        payload=DailyActivePayload(day_iso="2026-05-17"),
    )
    body = _decode(env)
    # Crude RFC3339 check.
    assert "T" in body["timestamp"]
    assert body["timestamp"].endswith("+00:00") or body["timestamp"].endswith("Z")


def test_serializer_payload_never_contains_secrets_or_args() -> None:
    """Closed set check: payloads only contain whitelisted keys."""
    allowed: set[str] = set()
    for cls in [
        InstallCompletedPayload,
        FirstRunStartedPayload,
        FirstRunCompletedPayload,
        CommandInvokedPayload,
        DailyActivePayload,
    ]:
        allowed.update(cls.__slots__)
    banned_substrings = {"prompt", "args", "secret", "token", "key", "password"}
    for k in allowed:
        for b in banned_substrings:
            assert b not in k.lower(), f"banned substring {b!r} in payload key {k!r}"
