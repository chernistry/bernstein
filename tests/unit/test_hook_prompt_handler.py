"""Tests for HOOK-006 — prompt handler type."""

from __future__ import annotations

import pytest

from bernstein.core.hook_events import HookEvent, HookPayload, TaskPayload
from bernstein.core.hook_prompt_handler import (
    PromptAggregator,
    PromptHookHandler,
    PromptInjection,
)


# ---------------------------------------------------------------------------
# PromptInjection
# ---------------------------------------------------------------------------


class TestPromptInjection:
    """PromptInjection data class."""

    def test_default_position_is_append(self) -> None:
        inj = PromptInjection(source="test", content="hello")
        assert inj.position == "append"

    def test_prepend_position(self) -> None:
        inj = PromptInjection(source="test", content="hello", position="prepend")
        assert inj.position == "prepend"

    def test_frozen(self) -> None:
        inj = PromptInjection(source="test", content="hello")
        with pytest.raises(AttributeError):
            inj.content = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PromptHookHandler
# ---------------------------------------------------------------------------


class TestPromptHookHandler:
    """PromptHookHandler renders templates and stores injections."""

    @pytest.mark.asyncio
    async def test_basic_template_rendering(self) -> None:
        handler = PromptHookHandler(
            name="test-hook",
            template="Event: {event}",
        )
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await handler(HookEvent.TASK_COMPLETED, payload)
        injections = handler.injections
        assert len(injections) == 1
        assert injections[0].content == "Event: task.completed"
        assert injections[0].source == "test-hook"

    @pytest.mark.asyncio
    async def test_template_with_payload_fields(self) -> None:
        handler = PromptHookHandler(
            name="task-hook",
            template="Task {task_id} for role {role}: {title}",
        )
        payload = TaskPayload(
            event=HookEvent.TASK_FAILED,
            task_id="t-42",
            role="backend",
            title="Fix bug",
        )
        await handler(HookEvent.TASK_FAILED, payload)
        assert handler.injections[0].content == "Task t-42 for role backend: Fix bug"

    @pytest.mark.asyncio
    async def test_unknown_placeholder_left_as_is(self) -> None:
        handler = PromptHookHandler(
            name="test",
            template="Unknown: {nonexistent_field}",
        )
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await handler(HookEvent.TASK_COMPLETED, payload)
        assert "{nonexistent_field}" in handler.injections[0].content

    @pytest.mark.asyncio
    async def test_position_preserved(self) -> None:
        handler = PromptHookHandler(
            name="test",
            template="prepended",
            position="prepend",
        )
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await handler(HookEvent.TASK_COMPLETED, payload)
        assert handler.injections[0].position == "prepend"

    @pytest.mark.asyncio
    async def test_multiple_calls_accumulate(self) -> None:
        handler = PromptHookHandler(name="test", template="call")
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await handler(HookEvent.TASK_COMPLETED, payload)
        await handler(HookEvent.TASK_COMPLETED, payload)
        assert len(handler.injections) == 2

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        handler = PromptHookHandler(name="test", template="call")
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await handler(HookEvent.TASK_COMPLETED, payload)
        handler.clear()
        assert len(handler.injections) == 0


# ---------------------------------------------------------------------------
# PromptAggregator
# ---------------------------------------------------------------------------


class TestPromptAggregator:
    """PromptAggregator combines injections into a final prompt."""

    def test_empty_aggregator_returns_base(self) -> None:
        agg = PromptAggregator()
        assert agg.build("base prompt") == "base prompt"

    def test_append_injections(self) -> None:
        agg = PromptAggregator()
        agg.add(PromptInjection(source="a", content="extra A"))
        agg.add(PromptInjection(source="b", content="extra B"))
        result = agg.build("base")
        assert result == "base\nextra A\nextra B"

    def test_prepend_injections(self) -> None:
        agg = PromptAggregator()
        agg.add(PromptInjection(source="a", content="before A", position="prepend"))
        agg.add(PromptInjection(source="b", content="before B", position="prepend"))
        result = agg.build("base")
        assert result == "before A\nbefore B\nbase"

    def test_mixed_positions(self) -> None:
        agg = PromptAggregator()
        agg.add(PromptInjection(source="pre", content="PREPEND", position="prepend"))
        agg.add(PromptInjection(source="post", content="APPEND", position="append"))
        result = agg.build("BASE")
        lines = result.split("\n")
        assert lines[0] == "PREPEND"
        assert lines[1] == "BASE"
        assert lines[2] == "APPEND"

    def test_add_all(self) -> None:
        agg = PromptAggregator()
        injections = [
            PromptInjection(source="a", content="1"),
            PromptInjection(source="b", content="2"),
        ]
        agg.add_all(injections)
        assert len(agg.injections) == 2

    def test_clear(self) -> None:
        agg = PromptAggregator()
        agg.add(PromptInjection(source="a", content="x"))
        agg.clear()
        assert len(agg.injections) == 0
        assert agg.build("base") == "base"
