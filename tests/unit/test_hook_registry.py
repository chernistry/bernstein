"""Tests for HOOK-004/008/009/011/012 — async hook registry."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bernstein.core.hook_events import HookEvent, HookPayload, TaskPayload
from bernstein.core.hook_registry import (
    DEFAULT_PRIORITY,
    AsyncHookRegistry,
    EventRecord,
    HandlerType,
    HookExecutionResult,
    HookFilter,
    HookMetrics,
    RegisteredHook,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(
    *,
    delay: float = 0.0,
    fail: bool = False,
    record: list[str] | None = None,
    name: str = "",
) -> Any:
    """Create a test async handler."""

    async def handler(event: HookEvent, payload: HookPayload) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        if record is not None:
            record.append(name or "handler")
        if fail:
            msg = "intentional failure"
            raise RuntimeError(msg)

    return handler


def _make_hook(
    name: str,
    events: frozenset[HookEvent] | None = None,
    priority: int = DEFAULT_PRIORITY,
    hook_filter: HookFilter | None = None,
    handler: Any | None = None,
    enabled: bool = True,
) -> RegisteredHook:
    """Create a RegisteredHook for testing."""
    return RegisteredHook(
        name=name,
        events=events or frozenset({HookEvent.TASK_COMPLETED}),
        handler_type=HandlerType.CALLABLE,
        handler=handler or _make_handler(),
        priority=priority,
        hook_filter=hook_filter,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# HookFilter (HOOK-007)
# ---------------------------------------------------------------------------


class TestHookFilter:
    """HookFilter matches contexts with glob patterns."""

    def test_empty_filter_matches_everything(self) -> None:
        f = HookFilter()
        assert f.matches({})
        assert f.matches({"role": "backend", "status": "open"})

    def test_role_glob_match(self) -> None:
        f = HookFilter(role="back*")
        assert f.matches({"role": "backend"})
        assert not f.matches({"role": "frontend"})

    def test_status_glob_match(self) -> None:
        f = HookFilter(status="fail*")
        assert f.matches({"status": "failed"})
        assert not f.matches({"status": "done"})

    def test_adapter_exact_match(self) -> None:
        f = HookFilter(adapter="claude")
        assert f.matches({"adapter": "claude"})
        assert not f.matches({"adapter": "codex"})

    def test_multiple_fields_all_must_match(self) -> None:
        f = HookFilter(role="back*", adapter="claude")
        assert f.matches({"role": "backend", "adapter": "claude"})
        assert not f.matches({"role": "backend", "adapter": "codex"})
        assert not f.matches({"role": "frontend", "adapter": "claude"})

    def test_missing_context_key_is_empty_string(self) -> None:
        f = HookFilter(role="*")
        assert f.matches({})  # empty string matches "*"

    def test_wildcard_matches_all(self) -> None:
        f = HookFilter(role="*", status="*", adapter="*")
        assert f.matches({"role": "anything", "status": "whatever", "adapter": "x"})


# ---------------------------------------------------------------------------
# HookMetrics (HOOK-009)
# ---------------------------------------------------------------------------


class TestHookMetrics:
    """HookMetrics tracks execution statistics."""

    def test_initial_state(self) -> None:
        m = HookMetrics(hook_name="test")
        assert m.total_calls == 0
        assert m.avg_latency_s == 0.0
        assert m.success_rate == 0.0
        assert m.error_rate == 0.0

    def test_record_success(self) -> None:
        m = HookMetrics(hook_name="test")
        m.record(0.1, success=True)
        assert m.total_calls == 1
        assert m.success_count == 1
        assert m.error_count == 0
        assert m.success_rate == 1.0
        assert m.error_rate == 0.0

    def test_record_failure(self) -> None:
        m = HookMetrics(hook_name="test")
        m.record(0.2, success=False)
        assert m.total_calls == 1
        assert m.error_count == 1
        assert m.error_rate == 1.0

    def test_mixed_calls(self) -> None:
        m = HookMetrics(hook_name="test")
        m.record(0.1, success=True)
        m.record(0.3, success=False)
        m.record(0.2, success=True)
        assert m.total_calls == 3
        assert m.success_count == 2
        assert m.error_count == 1
        assert m.success_rate == pytest.approx(2 / 3, rel=1e-3)
        assert m.error_rate == pytest.approx(1 / 3, rel=1e-3)

    def test_latency_tracking(self) -> None:
        m = HookMetrics(hook_name="test")
        m.record(0.1, success=True)
        m.record(0.5, success=True)
        m.record(0.3, success=True)
        assert m.min_latency_s == pytest.approx(0.1)
        assert m.max_latency_s == pytest.approx(0.5)
        assert m.avg_latency_s == pytest.approx(0.3)

    def test_to_dict(self) -> None:
        m = HookMetrics(hook_name="test")
        m.record(0.1, success=True)
        d = m.to_dict()
        assert d["hook_name"] == "test"
        assert d["total_calls"] == 1
        assert d["success_rate"] == 1.0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    """Hook registration and lookup."""

    def test_register_and_get(self) -> None:
        registry = AsyncHookRegistry()
        hook = _make_hook("h1")
        registry.register(hook)
        assert registry.get("h1") is hook

    def test_duplicate_name_raises(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_hook("h1"))

    def test_unregister(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        registry.unregister("h1")
        assert registry.get("h1") is None

    def test_unregister_missing_raises(self) -> None:
        registry = AsyncHookRegistry()
        with pytest.raises(KeyError):
            registry.unregister("nonexistent")

    def test_list_hooks_sorted_by_priority(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("low", priority=200))
        registry.register(_make_hook("high", priority=10))
        registry.register(_make_hook("mid", priority=100))
        hooks = registry.list_hooks()
        assert [h.name for h in hooks] == ["high", "mid", "low"]

    def test_list_hooks_ties_broken_by_name(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("beta", priority=100))
        registry.register(_make_hook("alpha", priority=100))
        hooks = registry.list_hooks()
        assert [h.name for h in hooks] == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# hooks_for_event with filters (HOOK-007/012)
# ---------------------------------------------------------------------------


class TestHooksForEvent:
    """hooks_for_event applies event matching, filters, and priority."""

    def test_matches_by_event(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1", events=frozenset({HookEvent.TASK_COMPLETED})))
        registry.register(_make_hook("h2", events=frozenset({HookEvent.TASK_FAILED})))
        matched = registry.hooks_for_event(HookEvent.TASK_COMPLETED)
        assert [h.name for h in matched] == ["h1"]

    def test_disabled_hooks_excluded(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1", enabled=False))
        matched = registry.hooks_for_event(HookEvent.TASK_COMPLETED)
        assert len(matched) == 0

    def test_filter_applied(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1", hook_filter=HookFilter(role="backend")))
        registry.register(_make_hook("h2", hook_filter=HookFilter(role="qa")))
        matched = registry.hooks_for_event(
            HookEvent.TASK_COMPLETED,
            context={"role": "backend"},
        )
        assert [h.name for h in matched] == ["h1"]

    def test_priority_ordering(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("low", priority=200))
        registry.register(_make_hook("high", priority=10))
        registry.register(_make_hook("mid", priority=100))
        matched = registry.hooks_for_event(HookEvent.TASK_COMPLETED)
        assert [h.name for h in matched] == ["high", "mid", "low"]

    def test_no_filter_matches_everything(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        matched = registry.hooks_for_event(
            HookEvent.TASK_COMPLETED,
            context={"role": "anything"},
        )
        assert len(matched) == 1


# ---------------------------------------------------------------------------
# Async dispatch (HOOK-004/008)
# ---------------------------------------------------------------------------


class TestAsyncDispatch:
    """dispatch fires handlers concurrently with metrics."""

    @pytest.mark.asyncio
    async def test_dispatch_calls_matching_handlers(self) -> None:
        registry = AsyncHookRegistry()
        called: list[str] = []
        registry.register(_make_hook("h1", handler=_make_handler(record=called, name="h1")))
        registry.register(_make_hook("h2", handler=_make_handler(record=called, name="h2")))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        results = await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        assert len(results) == 2
        assert all(r.success for r in results)
        assert set(called) == {"h1", "h2"}

    @pytest.mark.asyncio
    async def test_dispatch_records_metrics(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        metrics = registry.get_metrics("h1")
        assert metrics is not None
        assert metrics.total_calls == 1
        assert metrics.success_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_handles_errors(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("fail", handler=_make_handler(fail=True)))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        results = await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        assert len(results) == 1
        assert not results[0].success
        assert "intentional failure" in results[0].error
        metrics = registry.get_metrics("fail")
        assert metrics is not None
        assert metrics.error_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_no_matching_hooks_returns_empty(self) -> None:
        registry = AsyncHookRegistry()
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        results = await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        assert results == []

    @pytest.mark.asyncio
    async def test_priority_tiers_run_sequentially(self) -> None:
        """Hooks in lower priority number tiers run before higher."""
        registry = AsyncHookRegistry()
        execution_order: list[str] = []

        async def make_ordered(name: str) -> Any:
            async def handler(event: HookEvent, payload: HookPayload) -> None:
                execution_order.append(name)

            return handler

        h1_handler = await make_ordered("high")
        h2_handler = await make_ordered("low")

        registry.register(_make_hook("low", priority=200, handler=h2_handler))
        registry.register(_make_hook("high", priority=10, handler=h1_handler))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        assert execution_order[0] == "high"
        assert execution_order[1] == "low"

    @pytest.mark.asyncio
    async def test_concurrency_limit_respected(self) -> None:
        """No more than max_concurrency hooks run simultaneously."""
        registry = AsyncHookRegistry(max_concurrency=2)
        peak_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def tracking_handler(event: HookEvent, payload: HookPayload) -> None:
            nonlocal peak_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > peak_concurrent:
                    peak_concurrent = current_concurrent
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1

        for i in range(5):
            registry.register(_make_hook(f"h{i}", handler=tracking_handler))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        assert peak_concurrent <= 2

    @pytest.mark.asyncio
    async def test_dispatch_with_context_filter(self) -> None:
        registry = AsyncHookRegistry()
        called: list[str] = []
        registry.register(
            _make_hook(
                "backend-only",
                hook_filter=HookFilter(role="backend"),
                handler=_make_handler(record=called, name="backend"),
            )
        )
        registry.register(
            _make_hook(
                "any-role",
                handler=_make_handler(record=called, name="any"),
            )
        )
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(
            HookEvent.TASK_COMPLETED,
            payload,
            context={"role": "frontend"},
        )
        assert called == ["any"]


# ---------------------------------------------------------------------------
# Metrics API (HOOK-009)
# ---------------------------------------------------------------------------


class TestMetricsAPI:
    """Metrics collection and retrieval."""

    @pytest.mark.asyncio
    async def test_all_metrics(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("a"))
        registry.register(_make_hook("b", events=frozenset({HookEvent.TASK_FAILED})))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        all_m = registry.all_metrics()
        assert "a" in all_m
        assert all_m["a"].total_calls == 1

    def test_get_metrics_none(self) -> None:
        registry = AsyncHookRegistry()
        assert registry.get_metrics("nonexistent") is None

    @pytest.mark.asyncio
    async def test_reset_metrics(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        registry.reset_metrics()
        assert registry.get_metrics("h1") is None


# ---------------------------------------------------------------------------
# Event log and replay (HOOK-011)
# ---------------------------------------------------------------------------


class TestEventLogAndReplay:
    """Event recording and replay."""

    @pytest.mark.asyncio
    async def test_events_are_logged(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        log = registry.get_event_log()
        assert len(log) == 1
        assert log[0].event == HookEvent.TASK_COMPLETED

    @pytest.mark.asyncio
    async def test_event_log_filtered_by_event(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(
            _make_hook(
                "h1",
                events=frozenset({HookEvent.TASK_COMPLETED, HookEvent.TASK_FAILED}),
            )
        )
        await registry.dispatch(
            HookEvent.TASK_COMPLETED,
            HookPayload(event=HookEvent.TASK_COMPLETED),
        )
        await registry.dispatch(
            HookEvent.TASK_FAILED,
            HookPayload(event=HookEvent.TASK_FAILED),
        )
        filtered = registry.get_event_log(event_filter=HookEvent.TASK_FAILED)
        assert len(filtered) == 1

    @pytest.mark.asyncio
    async def test_replay_event(self) -> None:
        registry = AsyncHookRegistry()
        called: list[str] = []
        registry.register(_make_hook("h1", handler=_make_handler(record=called, name="h1")))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        called.clear()

        log = registry.get_event_log()
        results = await registry.replay_event(log[0])
        assert len(results) == 1
        assert called == ["h1"]

    @pytest.mark.asyncio
    async def test_clear_event_log(self) -> None:
        registry = AsyncHookRegistry()
        registry.register(_make_hook("h1"))
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        await registry.dispatch(HookEvent.TASK_COMPLETED, payload)
        registry.clear_event_log()
        assert len(registry.get_event_log()) == 0

    @pytest.mark.asyncio
    async def test_event_log_bounded(self) -> None:
        registry = AsyncHookRegistry(max_concurrency=1)
        registry._max_event_log = 10
        registry.register(_make_hook("h1"))
        for _ in range(15):
            await registry.dispatch(
                HookEvent.TASK_COMPLETED,
                HookPayload(event=HookEvent.TASK_COMPLETED),
            )
        # Should have trimmed oldest entries
        assert len(registry.get_event_log()) <= 10
