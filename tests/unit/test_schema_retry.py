"""Unit tests for the schema-validation retry helper."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bernstein.core.orchestration.manager_parsing import parse_tasks_response
from bernstein.core.tasks.schema_retry import (
    SchemaRetryContext,
    SchemaRetryExhausted,
    format_errors_for_prompt,
    validate_with_retry,
)


def _ok_validator(raw: str) -> dict[str, Any]:
    parsed: Any = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected object, got {type(parsed).__name__}")
    return parsed  # type: ignore[no-any-return]


def test_succeeds_on_first_attempt() -> None:
    ctx = SchemaRetryContext(step_id="t")
    calls: list[str] = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        return ""

    result = validate_with_retry(
        initial_response='{"ok": true}',
        validate=_ok_validator,
        ctx=ctx,
        ask_again=ask,
    )

    assert result == {"ok": True}
    assert ctx.attempts == []
    assert calls == []


def test_recovers_on_second_attempt_with_error_feedback() -> None:
    ctx = SchemaRetryContext(step_id="t")
    seen_prompts: list[str] = []

    def ask(prompt: str) -> str:
        seen_prompts.append(prompt)
        return '{"recovered": 1}'

    result = validate_with_retry(
        initial_response="not json",
        validate=_ok_validator,
        ctx=ctx,
        ask_again=ask,
    )

    assert result == {"recovered": 1}
    assert len(ctx.attempts) == 1
    assert ctx.attempts[0].attempt == 1
    assert "not json" in ctx.attempts[0].raw_response
    assert seen_prompts, "ask_again should have been called once"
    assert "previously" in seen_prompts[0].lower() or "fix" in seen_prompts[0].lower()


def test_terminal_failure_raises_with_full_trail() -> None:
    ctx = SchemaRetryContext(step_id="t")

    def ask(_prompt: str) -> str:
        return "still not json"

    with pytest.raises(SchemaRetryExhausted) as excinfo:
        validate_with_retry(
            initial_response="bad",
            validate=_ok_validator,
            ctx=ctx,
            ask_again=ask,
            max_attempts=3,
        )

    assert len(ctx.attempts) == 3
    assert all(a.error for a in ctx.attempts)
    assert excinfo.value.last_error
    assert "3 attempt" in str(excinfo.value)


def test_error_accumulation_across_steps() -> None:
    ctx = SchemaRetryContext(step_id="default")

    def ask_a(_prompt: str) -> str:
        return '{"a": 1}'

    # Step 1 — fail then recover.
    validate_with_retry(
        initial_response="bad-a",
        validate=_ok_validator,
        ctx=ctx,
        ask_again=ask_a,
        step_id="step.a",
    )

    # Step 2 — capture the prompt to confirm step.a's error is included.
    captured: list[str] = []

    def ask_b(prompt: str) -> str:
        captured.append(prompt)
        return '{"b": 2}'

    validate_with_retry(
        initial_response="bad-b",
        validate=_ok_validator,
        ctx=ctx,
        ask_again=ask_b,
        step_id="step.b",
    )

    assert any(a.step_id == "step.a" for a in ctx.attempts)
    assert any(a.step_id == "step.b" for a in ctx.attempts)
    assert "step.a" in captured[0]
    assert "step.b" in captured[0]


def test_format_errors_empty_when_no_attempts() -> None:
    ctx = SchemaRetryContext()
    assert format_errors_for_prompt(ctx) == ""


def test_format_errors_includes_step_and_attempt() -> None:
    ctx = SchemaRetryContext(step_id="foo")
    ctx.record(error="missing field 'title'", raw_response="{}")
    rendered = format_errors_for_prompt(ctx)

    assert "foo" in rendered
    assert "attempt 1" in rendered
    assert "missing field 'title'" in rendered


def test_max_attempts_must_be_positive() -> None:
    ctx = SchemaRetryContext()
    with pytest.raises(ValueError, match="max_attempts"):
        validate_with_retry(
            initial_response="x",
            validate=_ok_validator,
            ctx=ctx,
            ask_again=lambda _p: "x",
            max_attempts=0,
        )


def test_manager_parsing_retries_malformed_then_valid() -> None:
    valid_payload = json.dumps([{"title": "do thing", "role": "backend"}])
    ctx = SchemaRetryContext(step_id="manager.plan")

    def ask(_prompt: str) -> str:
        return valid_payload

    result = parse_tasks_response("```json\nnot really json\n```", ctx=ctx, ask_again=ask)

    assert result == [{"title": "do thing", "role": "backend"}]
    assert len(ctx.attempts) == 1
    assert ctx.attempts[0].step_id == "manager.plan"


def test_manager_parsing_one_shot_path_unchanged() -> None:
    valid_payload = json.dumps([{"title": "do thing"}])
    assert parse_tasks_response(valid_payload) == [{"title": "do thing"}]

    with pytest.raises(ValueError, match="not valid JSON"):
        parse_tasks_response("definitely not json")


def test_record_metric_does_not_break_when_prometheus_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.tasks import schema_retry as sr

    monkeypatch.setattr(sr, "_counter_singleton", None)
    monkeypatch.setattr(sr, "_counter_init_failed", True)

    ctx = SchemaRetryContext()
    result = validate_with_retry(
        initial_response='{"ok": 1}',
        validate=_ok_validator,
        ctx=ctx,
        ask_again=lambda _p: "",
    )
    assert result == {"ok": 1}


def test_prometheus_counter_increments_on_outcomes() -> None:
    """If prometheus_client is installed, the counter is created and labelled."""
    pytest.importorskip("prometheus_client")
    from bernstein.core.tasks import schema_retry as sr

    counter = sr._get_counter()
    assert counter is not None

    # Successful first-attempt path.
    ctx = SchemaRetryContext()
    validate_with_retry(
        initial_response='{"ok": 1}',
        validate=_ok_validator,
        ctx=ctx,
        ask_again=lambda _p: "",
    )

    # Exhausted path.
    with pytest.raises(SchemaRetryExhausted):
        validate_with_retry(
            initial_response="bad",
            validate=_ok_validator,
            ctx=SchemaRetryContext(),
            ask_again=lambda _p: "still bad",
            max_attempts=2,
        )
