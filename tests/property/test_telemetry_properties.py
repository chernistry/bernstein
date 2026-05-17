"""Property-based tests for the telemetry subsystem (Hypothesis)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bernstein.core.telemetry import (
    CommandInvokedPayload,
    DailyActivePayload,
    ErrorCategory,
    FirstRunCompletedPayload,
    FirstRunStartedPayload,
    InstallCompletedPayload,
    TelemetryEvent,
    build_envelope,
    ensure_install_id,
    serialize_event,
    write_enabled,
)

# ---------------------------------------------------------------------------
# Install id uniqueness
# ---------------------------------------------------------------------------


@given(st.integers(min_value=2, max_value=20))
@settings(max_examples=20, deadline=None)
def test_install_ids_are_unique_across_homes(tmp_path_factory: pytest.TempPathFactory, n: int) -> None:
    base = tmp_path_factory.mktemp("ids")
    ids: set[str] = set()
    for i in range(n):
        h = base / f"home-{i}"
        write_enabled(True, home=h)
        ids.add(ensure_install_id(home=h))
    assert len(ids) == n


@given(st.integers(min_value=1, max_value=5))
@settings(max_examples=10, deadline=None)
def test_ensure_install_id_idempotent(tmp_path_factory: pytest.TempPathFactory, calls: int) -> None:
    h = tmp_path_factory.mktemp("idem")
    write_enabled(True, home=h)
    first = ensure_install_id(home=h)
    for _ in range(calls):
        assert ensure_install_id(home=h) == first


# ---------------------------------------------------------------------------
# Event serializer invariants
# ---------------------------------------------------------------------------


@st.composite
def _install_completed_payloads(draw: st.DrawFn) -> InstallCompletedPayload:
    return InstallCompletedPayload(
        os=draw(st.sampled_from(["linux", "darwin", "windows"])),
        py_version=draw(st.from_regex(r"3\.[0-9]{1,2}\.[0-9]{1,2}", fullmatch=True)),
        install_method=draw(st.sampled_from(["pip", "uv", "brew", "wheel"])),
        bernstein_version=draw(st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True)),
    )


@st.composite
def _first_run_completed_payloads(draw: st.DrawFn) -> FirstRunCompletedPayload:
    ok = draw(st.booleans())
    category = None if ok else draw(st.sampled_from(list(ErrorCategory)))
    return FirstRunCompletedPayload(
        ok=ok,
        duration_ms=draw(st.integers(min_value=0, max_value=10**9)),
        error_category=category,
    )


@given(_install_completed_payloads())
@settings(max_examples=50)
def test_install_completed_round_trip(p: InstallCompletedPayload) -> None:
    env = build_envelope(
        TelemetryEvent.INSTALL_COMPLETED,
        install_id="x" * 32,
        payload=p,
        timestamp="2026-05-17T00:00:00+00:00",
    )
    body = json.loads(serialize_event(env))
    assert body["payload"]["os"] == p.os
    assert body["payload"]["py_version"] == p.py_version


@given(st.integers(min_value=0, max_value=10**9))
@settings(max_examples=30)
def test_first_run_started_serializer(time_since: int) -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_STARTED,
        install_id="x" * 32,
        payload=FirstRunStartedPayload(time_since_install_seconds=time_since),
    )
    body = json.loads(serialize_event(env))
    assert body["payload"]["time_since_install_seconds"] == time_since


@given(_first_run_completed_payloads())
@settings(max_examples=50)
def test_first_run_completed_serializer(p: FirstRunCompletedPayload) -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_COMPLETED,
        install_id="x" * 32,
        payload=p,
    )
    body = json.loads(serialize_event(env))
    assert body["payload"]["ok"] == p.ok
    assert body["payload"]["duration_ms"] == p.duration_ms
    if p.error_category is None:
        assert "error_category" not in body["payload"]
    else:
        assert body["payload"]["error_category"] == p.error_category.value


@given(st.text(min_size=1, max_size=64).filter(lambda s: s.strip()))
@settings(max_examples=30)
def test_command_invoked_payload_serializes(name: str) -> None:
    env = build_envelope(
        TelemetryEvent.COMMAND_INVOKED,
        install_id="x" * 32,
        payload=CommandInvokedPayload(name_only=name, bernstein_version="2.0.1"),
    )
    body = json.loads(serialize_event(env))
    assert body["payload"]["name_only"] == name


@given(st.dates())
@settings(max_examples=30)
def test_daily_active_payload_iso(day: object) -> None:
    iso = day.isoformat()  # pyright: ignore[reportAttributeAccessIssue]
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id="x" * 32,
        payload=DailyActivePayload(day_iso=iso),
    )
    body = json.loads(serialize_event(env))
    assert body["payload"]["day_iso"] == iso


@given(st.text(min_size=1, max_size=32))
@settings(max_examples=20)
def test_serializer_output_is_single_json_line(install_id: str) -> None:
    env = build_envelope(
        TelemetryEvent.DAILY_ACTIVE,
        install_id=install_id,
        payload=DailyActivePayload(day_iso="2026-05-17"),
    )
    line = serialize_event(env)
    assert "\n" not in line
    json.loads(line)  # must be valid json


@given(st.integers(min_value=0, max_value=10**9))
@settings(max_examples=20)
def test_duration_ms_never_negative_in_output(duration: int) -> None:
    env = build_envelope(
        TelemetryEvent.FIRST_RUN_COMPLETED,
        install_id="x" * 32,
        payload=FirstRunCompletedPayload(ok=True, duration_ms=duration),
    )
    body = json.loads(serialize_event(env))
    assert body["payload"]["duration_ms"] >= 0


@given(st.sampled_from(list(TelemetryEvent)))
@settings(max_examples=20)
def test_all_event_names_appear_in_closed_set(name: TelemetryEvent) -> None:
    assert name.value in {
        "install_completed",
        "first_run_started",
        "first_run_completed",
        "command_invoked",
        "daily_active",
    }
