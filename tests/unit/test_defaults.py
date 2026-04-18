"""Tests for bernstein.core.defaults override/reset mechanics."""

from __future__ import annotations

import pytest

from bernstein.core import defaults
from bernstein.core.defaults import override, reset


def test_override_scalar() -> None:
    reset()
    assert defaults.ORCHESTRATOR.tick_interval_s == pytest.approx(3.0)
    override("orchestrator", {"tick_interval_s": 5.0})
    assert defaults.ORCHESTRATOR.tick_interval_s == pytest.approx(5.0)
    reset()


def test_override_dict_merges() -> None:
    reset()
    override("task", {"scope_timeout_s": {"large": 7200}})
    assert defaults.TASK.scope_timeout_s["large"] == 7200
    assert defaults.TASK.scope_timeout_s["small"] == 900  # unchanged
    reset()


def test_override_invalid_section() -> None:
    with pytest.raises(KeyError):
        override("nonexistent", {"foo": 1})


def test_override_invalid_field() -> None:
    reset()
    with pytest.raises(AttributeError):
        override("orchestrator", {"bogus_field": 1})
    reset()


def test_janitor_defaults_documented() -> None:
    """audit-081: retention/rotation knobs must live in JanitorDefaults."""
    reset()
    assert defaults.JANITOR.run_retention_count == 20
    assert defaults.JANITOR.wal_retention_count == 50
    assert defaults.JANITOR.bridge_lineage_rotate_bytes > 0
    assert defaults.JANITOR.task_notifications_rotate_bytes > 0
    assert defaults.JANITOR.idempotency_rotate_bytes > 0
    assert defaults.JANITOR.file_health_rotate_bytes > 0
    assert defaults.JANITOR.file_health_touches_rotate_bytes > 0
    assert defaults.JANITOR.replay_rotate_bytes > 0


def test_janitor_override_round_trip() -> None:
    """``override("janitor", …)`` must tune retention without breaking reset()."""
    reset()
    try:
        override("janitor", {"run_retention_count": 5, "wal_retention_count": 7})
        assert defaults.JANITOR.run_retention_count == 5
        assert defaults.JANITOR.wal_retention_count == 7
    finally:
        reset()
    assert defaults.JANITOR.run_retention_count == 20
    assert defaults.JANITOR.wal_retention_count == 50
