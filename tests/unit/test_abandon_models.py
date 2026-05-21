"""Unit tests for the abandonment dataclass and reason enum (#1350)."""

from __future__ import annotations

import json
import time

import pytest

from bernstein.core.tasks.abandon import (
    Abandonment,
    AbandonReason,
    new_abandonment,
)

# ---------------------------------------------------------------------------
# AbandonReason enum
# ---------------------------------------------------------------------------


class TestAbandonReasonEnum:
    @pytest.mark.parametrize(
        "value",
        [
            "out_of_scope",
            "insufficient_context",
            "conflicting_instructions",
            "spec_underdetermined",
            "time_budget_exhausted",
            "budget_exceeded",
            "capability_mismatch",
            "env_broken",
            "blocked_by_external",
            "unsafe_change",
            "operator_override",
            "other",
        ],
    )
    def test_every_taxonomy_value_round_trips(self, value: str) -> None:
        reason = AbandonReason(value)
        assert reason.value == value
        assert AbandonReason.coerce(value) is reason

    def test_coerce_accepts_enum_instance(self) -> None:
        assert AbandonReason.coerce(AbandonReason.OUT_OF_SCOPE) is AbandonReason.OUT_OF_SCOPE

    def test_coerce_accepts_enum_name_case_insensitive(self) -> None:
        assert AbandonReason.coerce("OUT_OF_SCOPE") is AbandonReason.OUT_OF_SCOPE
        assert AbandonReason.coerce("out_of_scope") is AbandonReason.OUT_OF_SCOPE
        assert AbandonReason.coerce("Out_Of_Scope") is AbandonReason.OUT_OF_SCOPE

    def test_coerce_strips_whitespace(self) -> None:
        assert AbandonReason.coerce("  out_of_scope  ") is AbandonReason.OUT_OF_SCOPE

    @pytest.mark.parametrize("bad", ["unknown", "abandon", "scope", "x", "OUT-OF-SCOPE"])
    def test_coerce_rejects_unknown_values(self, bad: str) -> None:
        with pytest.raises(ValueError, match="Unknown AbandonReason"):
            AbandonReason.coerce(bad)

    def test_coerce_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            AbandonReason.coerce("")

    def test_coerce_rejects_whitespace_only(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            AbandonReason.coerce("   ")

    def test_coerce_rejects_non_string(self) -> None:
        with pytest.raises(ValueError, match="must be str"):
            AbandonReason.coerce(42)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must be str"):
            AbandonReason.coerce(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="must be str"):
            AbandonReason.coerce(["out_of_scope"])  # type: ignore[arg-type]

    def test_taxonomy_size_is_twelve(self) -> None:
        # Locks the taxonomy size; adding a new reason should be a
        # conscious change visible in this assertion's diff.
        assert len(list(AbandonReason)) == 12

    def test_all_values_are_snake_case(self) -> None:
        for member in AbandonReason:
            assert member.value.islower()
            assert " " not in member.value
            assert "\u2014" not in member.value


# ---------------------------------------------------------------------------
# Abandonment dataclass
# ---------------------------------------------------------------------------


class TestAbandonmentDataclass:
    def _row(self, **overrides: object) -> Abandonment:
        base: dict[str, object] = {
            "id": "abc123",
            "task_id": "T-001",
            "reason": AbandonReason.OUT_OF_SCOPE,
            "detail": "spec disagrees with test fixtures",
            "role": "backend",
            "agent_id": "session-1",
            "adapter": "claude",
            "cost_to_date_usd": 0.42,
            "attempts": 1,
            "timestamp": 1_700_000_000.0,
        }
        base.update(overrides)
        return Abandonment(**base)  # type: ignore[arg-type]

    def test_construction_sets_fields(self) -> None:
        row = self._row()
        assert row.id == "abc123"
        assert row.task_id == "T-001"
        assert row.reason is AbandonReason.OUT_OF_SCOPE
        assert row.attempts == 1
        assert row.adapter == "claude"

    def test_to_dict_produces_json_safe_values(self) -> None:
        row = self._row()
        data = row.to_dict()
        # Serialise to ensure JSON-compatibility
        encoded = json.dumps(data, sort_keys=True)
        round_tripped = json.loads(encoded)
        assert round_tripped["reason"] == "out_of_scope"
        assert round_tripped["cost_to_date_usd"] == pytest.approx(0.42)
        assert round_tripped["timestamp"] == 1_700_000_000.0

    def test_from_dict_round_trip(self) -> None:
        row = self._row()
        data = row.to_dict()
        restored = Abandonment.from_dict(data)
        assert restored == row

    def test_from_dict_accepts_enum_name_in_reason(self) -> None:
        row = Abandonment.from_dict(
            {
                "id": "x",
                "task_id": "T-1",
                "reason": "OUT_OF_SCOPE",
            }
        )
        assert row.reason is AbandonReason.OUT_OF_SCOPE

    def test_from_dict_rejects_unknown_reason(self) -> None:
        with pytest.raises(ValueError, match="Unknown AbandonReason"):
            Abandonment.from_dict({"id": "x", "task_id": "T-1", "reason": "stage_fright"})

    def test_from_dict_missing_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="missing task_id"):
            Abandonment.from_dict({"id": "x", "reason": "out_of_scope"})

    def test_from_dict_missing_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="missing reason"):
            Abandonment.from_dict({"id": "x", "task_id": "T-1"})

    def test_from_dict_generates_id_when_missing(self) -> None:
        row = Abandonment.from_dict({"task_id": "T-1", "reason": "other"})
        assert row.id  # non-empty
        assert len(row.id) >= 8

    def test_from_dict_tolerates_extra_keys(self) -> None:
        row = Abandonment.from_dict(
            {
                "id": "x",
                "task_id": "T-1",
                "reason": "other",
                "future_field": "ignored-without-error",
            }
        )
        assert row.task_id == "T-1"

    def test_from_dict_coerces_numeric_strings(self) -> None:
        row = Abandonment.from_dict(
            {
                "id": "x",
                "task_id": "T-1",
                "reason": "other",
                "cost_to_date_usd": "1.5",
                "attempts": "2",
                "timestamp": "1700000000",
            }
        )
        assert row.cost_to_date_usd == pytest.approx(1.5)
        assert row.attempts == 2
        assert row.timestamp == 1_700_000_000.0

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="id must be non-empty"):
            self._row(id="")

    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="task_id must be non-empty"):
            self._row(task_id="")

    def test_non_enum_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an AbandonReason"):
            self._row(reason="out_of_scope")  # str, not enum

    def test_negative_attempts_rejected(self) -> None:
        with pytest.raises(ValueError, match="attempts must be >= 0"):
            self._row(attempts=-1)

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost_to_date_usd must be >= 0"):
            self._row(cost_to_date_usd=-0.01)

    def test_negative_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="timestamp must be >= 0"):
            self._row(timestamp=-1.0)

    def test_zero_attempts_and_cost_allowed(self) -> None:
        row = self._row(attempts=0, cost_to_date_usd=0.0)
        assert row.attempts == 0
        assert row.cost_to_date_usd == 0.0

    def test_frozen_dataclass_is_immutable(self) -> None:
        row = self._row()
        with pytest.raises((AttributeError, TypeError)):
            row.task_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# new_abandonment factory
# ---------------------------------------------------------------------------


class TestNewAbandonmentFactory:
    def test_creates_with_fresh_id(self) -> None:
        r1 = new_abandonment(task_id="T-1", reason=AbandonReason.OTHER)
        r2 = new_abandonment(task_id="T-1", reason=AbandonReason.OTHER)
        assert r1.id != r2.id

    def test_accepts_string_reason(self) -> None:
        row = new_abandonment(task_id="T-1", reason="out_of_scope")
        assert row.reason is AbandonReason.OUT_OF_SCOPE

    def test_rejects_unknown_string_reason(self) -> None:
        with pytest.raises(ValueError, match="Unknown AbandonReason"):
            new_abandonment(task_id="T-1", reason="not_a_reason")

    def test_default_timestamp_is_recent(self) -> None:
        before = time.time()
        row = new_abandonment(task_id="T-1", reason=AbandonReason.OTHER)
        after = time.time()
        assert before <= row.timestamp <= after

    def test_explicit_timestamp_is_honoured(self) -> None:
        row = new_abandonment(task_id="T-1", reason=AbandonReason.OTHER, timestamp=42.0)
        assert row.timestamp == 42.0

    def test_factory_propagates_all_optional_fields(self) -> None:
        row = new_abandonment(
            task_id="T-1",
            reason=AbandonReason.BUDGET_EXCEEDED,
            detail="cost cap reached",
            role="qa",
            agent_id="sess-9",
            adapter="codex",
            cost_to_date_usd=12.34,
            attempts=2,
        )
        assert row.detail == "cost cap reached"
        assert row.role == "qa"
        assert row.agent_id == "sess-9"
        assert row.adapter == "codex"
        assert row.cost_to_date_usd == pytest.approx(12.34)
        assert row.attempts == 2
