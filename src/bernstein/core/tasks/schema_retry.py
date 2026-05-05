"""Schema-validation retry with cross-step error accumulation.

A small, dependency-light helper that lets call sites which parse
structured output from spawned agents (manager planning JSON, MCP tool
results, planner-output decoder, etc.) retry the agent up to N times,
feeding the **specific validation error** back into the next prompt.
Errors accumulate across steps so the agent can see "you keep mis-typing
field X".

The retry loop itself is the repair mechanism — there is no LLM-based
schema repair here.  The pattern is documented as Self-Refine
(ICLR 2024) and is reported to improve structured-output quality by
15-45%.

Usage::

    from bernstein.core.tasks.schema_retry import (
        SchemaRetryContext,
        validate_with_retry,
    )

    ctx = SchemaRetryContext(step_id="manager.plan")

    def ask_again(prompt: str) -> str:
        return spawn_one_shot(prompt)

    payload = validate_with_retry(
        initial_response=raw_text,
        validate=lambda s: parse_tasks_response(s),
        ctx=ctx,
        ask_again=ask_again,
    )

``validate`` is any callable that either returns a parsed value or
raises :class:`ValueError` (or any subclass) describing the failure.
On exhaustion :class:`SchemaRetryExhausted` is raised with the full
error trail attached.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bernstein.core.defaults import SCHEMA_RETRY

logger = logging.getLogger(__name__)


AskAgain = Callable[[str], str]
"""Pluggable callable: takes a prompt with accumulated errors, returns raw text."""

type Validator[T] = Callable[[str], T]
"""Validator callable: parses raw text and raises ``ValueError`` on schema failure."""


@dataclass(frozen=True)
class SchemaRetryAttempt:
    """One attempt in the retry loop.

    Attributes:
        step_id: Logical step that produced this attempt (e.g. ``"manager.plan"``).
        attempt: 1-indexed attempt number within the step.
        error: Validation error message (empty for successful attempts).
        raw_response: Raw response that was validated.
    """

    step_id: str
    attempt: int
    error: str
    raw_response: str


@dataclass
class SchemaRetryContext:
    """Accumulates validation failures across one or more steps.

    Pass the same context to multiple :func:`validate_with_retry` calls
    in the same workflow to give the agent visibility into failures it
    made during *earlier* steps as well as the current one.

    Attributes:
        step_id: Default step id used when none is supplied to ``record``.
        attempts: Ordered list of all attempts recorded so far.
    """

    step_id: str = "default"
    attempts: list[SchemaRetryAttempt] = field(default_factory=list[SchemaRetryAttempt])

    def record(self, *, error: str, raw_response: str, step_id: str | None = None) -> None:
        """Record one failed attempt.

        Args:
            error: Validation error message.
            raw_response: The raw text that failed validation.
            step_id: Override the default step id for this attempt.
        """
        sid = step_id if step_id is not None else self.step_id
        next_attempt = sum(1 for a in self.attempts if a.step_id == sid) + 1
        self.attempts.append(
            SchemaRetryAttempt(
                step_id=sid,
                attempt=next_attempt,
                error=error,
                raw_response=raw_response,
            )
        )

    def errors_for(self, step_id: str) -> list[SchemaRetryAttempt]:
        """Return attempts recorded for a specific step."""
        return [a for a in self.attempts if a.step_id == step_id]


class SchemaRetryExhausted(Exception):
    """Raised when ``max_attempts`` validation attempts have all failed.

    Attributes:
        ctx: The retry context, with the full attempt trail.
        last_error: The final validation error message.
    """

    def __init__(self, ctx: SchemaRetryContext, last_error: str) -> None:
        self.ctx = ctx
        self.last_error = last_error
        super().__init__(f"Schema validation failed after {len(ctx.attempts)} attempt(s); last error: {last_error}")


def format_errors_for_prompt(ctx: SchemaRetryContext) -> str:
    """Render the accumulated errors as a prompt preamble.

    The preamble is empty when no errors have been recorded yet so it
    can be unconditionally prepended.

    Args:
        ctx: The retry context.

    Returns:
        A multi-line string ready to prepend to the next prompt, or an
        empty string when there are no errors yet.
    """
    if not ctx.attempts:
        return ""
    lines = ["Your previous response(s) failed validation. Fix these errors:"]
    for att in ctx.attempts:
        lines.append(f"  - [{att.step_id} attempt {att.attempt}] {att.error}")
    lines.append("")
    return "\n".join(lines)


def validate_with_retry[T](
    initial_response: str,
    validate: Validator[T],
    ctx: SchemaRetryContext,
    *,
    ask_again: AskAgain,
    max_attempts: int | None = None,
    step_id: str | None = None,
    base_prompt: str = "",
) -> T:
    """Validate ``initial_response`` and retry on failure with error feedback.

    The first attempt uses ``initial_response`` directly.  On
    :class:`ValueError`, the error is recorded, and a new prompt
    (``base_prompt`` plus :func:`format_errors_for_prompt`) is fed to
    ``ask_again``; its return value is the next attempt.  After
    ``max_attempts`` failures, :class:`SchemaRetryExhausted` is raised.

    Args:
        initial_response: Raw text from the first agent call.
        validate: Callable that parses the response or raises ``ValueError``.
        ctx: Context that accumulates errors across calls.
        ask_again: Pluggable spawner — receives a prompt, returns raw text.
        max_attempts: Override the configured default.
        step_id: Override ``ctx.step_id`` for attempts recorded by this call.
        base_prompt: Optional prompt body appended after the error preamble.

    Returns:
        Whatever ``validate`` returns on success.

    Raises:
        SchemaRetryExhausted: If all attempts fail validation.
    """
    attempts_cap = max_attempts if max_attempts is not None else SCHEMA_RETRY.max_attempts
    if attempts_cap < 1:
        raise ValueError(f"max_attempts must be >= 1, got {attempts_cap}")

    sid = step_id if step_id is not None else ctx.step_id
    response = initial_response
    last_error = ""

    for attempt_num in range(1, attempts_cap + 1):
        try:
            result = validate(response)
        except ValueError as exc:
            last_error = str(exc)
            ctx.record(error=last_error, raw_response=response, step_id=sid)
            _record_metric("retry" if attempt_num < attempts_cap else "exhausted")
            if attempt_num >= attempts_cap:
                logger.warning(
                    "Schema validation exhausted for step=%s after %d attempts; last error: %s",
                    sid,
                    attempt_num,
                    last_error,
                )
                raise SchemaRetryExhausted(ctx, last_error) from exc
            preamble = format_errors_for_prompt(ctx)
            next_prompt = preamble + base_prompt if base_prompt else preamble
            logger.info(
                "Schema validation failed for step=%s attempt=%d; retrying. Error: %s",
                sid,
                attempt_num,
                last_error,
            )
            response = ask_again(next_prompt)
            continue
        else:
            _record_metric("success" if attempt_num == 1 else "recovered")
            return result

    # Unreachable: the loop either returns or raises.
    raise SchemaRetryExhausted(ctx, last_error)


def _record_metric(outcome: str) -> None:
    """Increment the schema_retry_attempts_total Prometheus counter.

    The counter is created lazily so the module stays importable even
    when ``prometheus_client`` is not installed (Windows / minimal
    installs).  Failures are swallowed — metrics must never break the
    retry loop.
    """
    counter: Any = _get_counter()
    if counter is None:
        return
    try:
        counter.labels(outcome=outcome).inc()
    except Exception:  # pragma: no cover
        # Metrics must never break the retry loop.
        logger.debug("schema_retry_attempts_total increment failed", exc_info=True)


_counter_singleton: Any = None
_counter_init_failed = False


def _get_counter() -> Any:
    """Lazy-init the Prometheus counter on the shared registry.

    Returns ``None`` if ``prometheus_client`` is unavailable or the
    metric could not be registered.
    """
    global _counter_singleton, _counter_init_failed
    if _counter_singleton is not None:
        return _counter_singleton
    if _counter_init_failed:
        return None
    try:
        from prometheus_client import Counter

        from bernstein.core.observability.prometheus import registry as _shared_registry

        _counter_singleton = Counter(
            "schema_retry_attempts_total",
            "Schema-validation retry attempts by terminal outcome.",
            labelnames=["outcome"],
            registry=_shared_registry,  # type: ignore[arg-type]  # stub or real registry
        )
    except Exception:
        _counter_init_failed = True
        return None
    return _counter_singleton
