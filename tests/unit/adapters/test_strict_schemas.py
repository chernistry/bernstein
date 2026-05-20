"""Unit tests for the strict structured-output schema primitives.

Covers the three contract guarantees:

* forbid: extra keys are rejected, never silently dropped;
* blacklist: operator-owned fields are stripped before merge and the
  attempt is recorded;
* provider error classification: additional-property rejections map to a
  bounded :class:`SchemaViolation`.
"""

from __future__ import annotations

import logging

import pytest

from bernstein.adapters.strict_schema import (
    USER_OWNED_FIELDS,
    AIWriteRejected,
    SchemaViolation,
    UserOwnedFieldRegistry,
    assert_schema_sealed,
    classify_schema_violation,
    seal_schema,
    strip_user_owned_fields,
)
from bernstein.core.orchestration.refinement_schemas import (
    CRITIQUE_SCHEMA_ID,
    Critique,
)

# ---------------------------------------------------------------------------
# forbid: extra keys rejected
# ---------------------------------------------------------------------------


def test_seal_schema_seals_object_nodes() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"inner": {"type": "string"}},
            },
        },
    }
    sealed = seal_schema(schema)
    assert sealed["additionalProperties"] is False
    assert sealed["properties"]["nested"]["additionalProperties"] is False
    # Original is not mutated.
    assert "additionalProperties" not in schema


def test_seal_schema_respects_explicit_open_node() -> None:
    schema = {
        "type": "object",
        "properties": {"freeform": {"type": "object", "additionalProperties": True}},
    }
    sealed = seal_schema(schema)
    assert sealed["properties"]["freeform"]["additionalProperties"] is True


def test_assert_schema_sealed_passes_for_sealed_schema() -> None:
    schema = seal_schema({"type": "object", "properties": {"name": {"type": "string"}}})
    assert_schema_sealed(schema)  # must not raise


def test_assert_schema_sealed_rejects_open_schema() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    with pytest.raises(ValueError, match="additionalProperties"):
        assert_schema_sealed(schema)


def test_critique_strict_rejects_extra_field() -> None:
    payload = {
        "score": 0.5,
        "issues": [],
        "veto": False,
        "rationale": "ok",
        "hallucinated": "leak",
    }
    with pytest.raises(SchemaViolation) as excinfo:
        Critique.from_dict_strict(payload)
    assert "hallucinated" in excinfo.value.fields


def test_critique_strict_rejects_extra_field_inside_issue() -> None:
    payload = {
        "score": 0.5,
        "issues": [{"severity": "low", "message": "x", "rogue": "leak"}],
    }
    with pytest.raises(SchemaViolation) as excinfo:
        Critique.from_dict_strict(payload)
    assert "rogue" in excinfo.value.fields


# ---------------------------------------------------------------------------
# valid passes
# ---------------------------------------------------------------------------


def test_critique_strict_accepts_valid_payload() -> None:
    payload = {
        "score": 0.8,
        "issues": [{"severity": "high", "message": "fix the parse path"}],
        "veto": False,
        "rationale": "almost there",
    }
    critique = Critique.from_dict_strict(payload)
    assert critique.score == pytest.approx(0.8)
    assert critique.issues[0].severity == "high"


def test_critique_strict_accepts_minimal_payload() -> None:
    critique = Critique.from_dict_strict({"score": 0.0})
    assert critique.score == 0.0
    assert critique.issues == []


# ---------------------------------------------------------------------------
# blacklist: operator-owned fields stripped
# ---------------------------------------------------------------------------


def test_strip_user_owned_fields_removes_blacklisted_keys() -> None:
    update = {
        "summary": "model wrote this",
        "id": "task-123",
        "user_notes": "operator only",
        "created_at": "2026-01-01",
    }
    safe, rejected = strip_user_owned_fields(CRITIQUE_SCHEMA_ID, update)
    assert safe == {"summary": "model wrote this"}
    assert rejected is not None
    assert rejected.schema == CRITIQUE_SCHEMA_ID
    assert set(rejected.fields) == {"id", "user_notes", "created_at"}


def test_strip_user_owned_fields_passes_clean_update() -> None:
    update = {"summary": "clean", "decisions": ["a"]}
    safe, rejected = strip_user_owned_fields(CRITIQUE_SCHEMA_ID, update)
    assert safe == update
    assert safe is not update  # a copy, never the caller's mapping
    assert rejected is None


def test_strip_user_owned_fields_logs_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    update = {"summary": "x", "operator_overrides": {"priority": "high"}}
    with caplog.at_level(logging.WARNING, logger="bernstein.adapters.strict_schema"):
        _, rejected = strip_user_owned_fields("schema://x", update)
    assert rejected is not None
    assert any("AIWriteRejected" in record.message for record in caplog.records)


def test_registry_unions_with_global_default() -> None:
    registry = UserOwnedFieldRegistry()
    registry.register("schema://x", {"tags"})
    fields = registry.fields_for("schema://x")
    assert "tags" in fields
    # Global floor is never weakened by a per-schema registration.
    assert fields >= USER_OWNED_FIELDS


def test_registry_default_floor_for_unregistered_schema() -> None:
    registry = UserOwnedFieldRegistry()
    assert registry.fields_for("schema://unknown") == USER_OWNED_FIELDS


def test_ai_write_rejected_str_names_schema_and_fields() -> None:
    record = AIWriteRejected(schema="schema://x", fields=("id", "user_notes"))
    text = str(record)
    assert "schema://x" in text
    assert "id" in text
    assert "user_notes" in text


# ---------------------------------------------------------------------------
# provider error classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "Additional properties are not allowed ('rogue' was unexpected)",
        "response did not match schema: additionalProperties 'extra'",
        "extra fields not permitted",
        "__init__() got an unexpected keyword argument 'rogue'",
    ],
)
def test_classify_schema_violation_detects_additional_properties(message: str) -> None:
    violation = classify_schema_violation(message)
    assert isinstance(violation, SchemaViolation)


def test_classify_schema_violation_lifts_field_names() -> None:
    violation = classify_schema_violation("Additional properties are not allowed ('rogue' was unexpected)")
    assert violation is not None
    assert "rogue" in violation.fields


def test_classify_schema_violation_ignores_unrelated_error() -> None:
    assert classify_schema_violation("connection reset by peer") is None


def test_classify_schema_violation_accepts_exception() -> None:
    err = ValueError("additionalProperties: 'x' not permitted")
    violation = classify_schema_violation(err)
    assert isinstance(violation, SchemaViolation)
