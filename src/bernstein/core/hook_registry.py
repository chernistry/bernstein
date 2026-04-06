"""HOOK-004/008/009/012: Async hook registry with concurrent execution.

Provides a unified event bus that dispatches hook events to registered
handlers.  Supports:

- Multiple handlers per event, fired concurrently with configurable concurrency
- Priority ordering (higher priority runs first)
- Execution metrics tracking (latency, success/error rates)
- Pattern matching filters (role, status, adapter globs)
- Exec, prompt, and callable handler types
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from bernstein.core.hook_events import HookEvent, HookPayload

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Handler types
# ---------------------------------------------------------------------------


@unique
class HandlerType(Enum):
    """Discriminator for hook handler implementations."""

    CALLABLE = "callable"
    EXEC = "exec"
    PROMPT = "prompt"


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

DEFAULT_PRIORITY: int = 100
"""Default priority for hooks. Lower number = higher priority (runs first)."""


# ---------------------------------------------------------------------------
# Pattern filter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookFilter:
    """Glob-based filter to restrict when a hook fires.

    All fields use ``fnmatch``-style glob patterns.  A ``None`` field
    means "match everything".

    Attributes:
        role: Glob pattern matching the task/agent role (e.g. ``"backend*"``).
        status: Glob pattern matching the task status (e.g. ``"fail*"``).
        adapter: Glob pattern matching the CLI adapter (e.g. ``"claude"``).
    """

    role: str | None = None
    status: str | None = None
    adapter: str | None = None

    def matches(self, context: dict[str, str]) -> bool:
        """Return True if this filter matches the given context.

        Args:
            context: Dict with optional ``role``, ``status``, ``adapter`` keys.

        Returns:
            True if every non-None filter field matches its context value.
        """
        import fnmatch

        for attr in ("role", "status", "adapter"):
            pattern = getattr(self, attr)
            if pattern is None:
                continue
            value = context.get(attr, "")
            if not fnmatch.fnmatch(value, pattern):
                return False
        return True


# ---------------------------------------------------------------------------
# Hook execution metrics (HOOK-009)
# ---------------------------------------------------------------------------


@dataclass
class HookMetrics:
    """Per-hook execution metrics.

    Attributes:
        hook_name: Name of the hook.
        total_calls: Total number of invocations.
        success_count: Number of successful invocations.
        error_count: Number of failed invocations.
        total_latency_s: Sum of all execution durations in seconds.
        min_latency_s: Minimum execution duration.
        max_latency_s: Maximum execution duration.
    """

    hook_name: str = ""
    total_calls: int = 0
    success_count: int = 0
    error_count: int = 0
    total_latency_s: float = 0.0
    min_latency_s: float = float("inf")
    max_latency_s: float = 0.0

    @property
    def avg_latency_s(self) -> float:
        """Average execution latency in seconds."""
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_s / self.total_calls

    @property
    def success_rate(self) -> float:
        """Fraction of calls that succeeded (0.0-1.0)."""
        if self.total_calls == 0:
            return 0.0
        return self.success_count / self.total_calls

    @property
    def error_rate(self) -> float:
        """Fraction of calls that failed (0.0-1.0)."""
        if self.total_calls == 0:
            return 0.0
        return self.error_count / self.total_calls

    def record(self, duration_s: float, *, success: bool) -> None:
        """Record the outcome of a single hook execution.

        Args:
            duration_s: Wall-clock seconds the hook took.
            success: Whether the hook succeeded.
        """
        self.total_calls += 1
        self.total_latency_s += duration_s
        if duration_s < self.min_latency_s:
            self.min_latency_s = duration_s
        if duration_s > self.max_latency_s:
            self.max_latency_s = duration_s
        if success:
            self.success_count += 1
        else:
            self.error_count += 1

    def to_dict(self) -> dict[str, Any]:
        """Serialise metrics to a JSON-friendly dict."""
        return {
            "hook_name": self.hook_name,
            "total_calls": self.total_calls,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "avg_latency_s": round(self.avg_latency_s, 6),
            "min_latency_s": round(self.min_latency_s, 6) if self.total_calls > 0 else 0.0,
            "max_latency_s": round(self.max_latency_s, 6),
            "success_rate": round(self.success_rate, 4),
            "error_rate": round(self.error_rate, 4),
        }


# ---------------------------------------------------------------------------
# Hook handler protocol
# ---------------------------------------------------------------------------


class AsyncHookHandler(Protocol):
    """Callable signature for async hook handlers."""

    async def __call__(self, event: HookEvent, payload: HookPayload) -> None: ...


# ---------------------------------------------------------------------------
# Hook execution result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookExecutionResult:
    """Result from executing a single hook handler.

    Attributes:
        hook_name: Name of the hook that was executed.
        success: Whether the hook completed without error.
        duration_s: Wall-clock seconds the hook took.
        error: Error message if the hook failed.
        output: Captured output (stdout for exec handlers).
    """

    hook_name: str
    success: bool
    duration_s: float
    error: str = ""
    output: str = ""


# ---------------------------------------------------------------------------
# Registered hook entry
# ---------------------------------------------------------------------------


@dataclass
class RegisteredHook:
    """A hook handler registered in the registry.

    Attributes:
        name: Unique name for this hook registration.
        events: Set of events this hook listens to.
        handler_type: The kind of handler (callable, exec, prompt).
        handler: The async callable to execute.
        priority: Execution priority (lower = runs first).
        hook_filter: Optional filter restricting when the hook fires.
        enabled: Whether the hook is active.
    """

    name: str
    events: frozenset[HookEvent]
    handler_type: HandlerType
    handler: AsyncHookHandler
    priority: int = DEFAULT_PRIORITY
    hook_filter: HookFilter | None = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# Event record for replay (HOOK-011)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventRecord:
    """Persisted record of a dispatched event for replay.

    Attributes:
        event: The hook event that was dispatched.
        payload: The payload that was dispatched.
        timestamp: Unix epoch seconds when the event was dispatched.
        context: Filter context that was active during dispatch.
    """

    event: HookEvent
    payload: HookPayload
    timestamp: float = field(default_factory=time.time)
    context: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Async Hook Registry (HOOK-004/008)
# ---------------------------------------------------------------------------


class AsyncHookRegistry:
    """Central registry for hook handlers with concurrent async dispatch.

    Provides a unified event bus that replaces separate pluggy and
    Claude Code hook dispatchers.

    Args:
        max_concurrency: Maximum number of hooks that fire in parallel
            for a single event dispatch.
    """

    def __init__(self, max_concurrency: int = 10) -> None:
        self._hooks: dict[str, RegisteredHook] = {}
        self._max_concurrency = max_concurrency
        self._metrics: dict[str, HookMetrics] = {}
        self._event_log: list[EventRecord] = []
        self._max_event_log: int = 10000

    @property
    def max_concurrency(self) -> int:
        """Return the configured max concurrency."""
        return self._max_concurrency

    def register(self, hook: RegisteredHook) -> None:
        """Register a hook handler.

        Args:
            hook: The hook registration entry.

        Raises:
            ValueError: If a hook with the same name is already registered.
        """
        if hook.name in self._hooks:
            msg = f"Hook {hook.name!r} is already registered"
            raise ValueError(msg)
        self._hooks[hook.name] = hook
        if hook.name not in self._metrics:
            self._metrics[hook.name] = HookMetrics(hook_name=hook.name)

    def unregister(self, name: str) -> None:
        """Remove a hook by name.

        Args:
            name: The hook name to remove.

        Raises:
            KeyError: If no hook with that name exists.
        """
        if name not in self._hooks:
            msg = f"No hook registered with name {name!r}"
            raise KeyError(msg)
        del self._hooks[name]

    def get(self, name: str) -> RegisteredHook | None:
        """Look up a hook by name.

        Args:
            name: The hook name to find.

        Returns:
            The registered hook, or None.
        """
        return self._hooks.get(name)

    def list_hooks(self) -> list[RegisteredHook]:
        """Return all registered hooks sorted by priority then name."""
        return sorted(
            self._hooks.values(),
            key=lambda h: (h.priority, h.name),
        )

    def hooks_for_event(
        self,
        event: HookEvent,
        context: dict[str, str] | None = None,
    ) -> list[RegisteredHook]:
        """Return hooks that match an event and optional context.

        Hooks are returned sorted by priority (lowest number first),
        then alphabetically by name for deterministic ordering.

        Args:
            event: The event to match.
            context: Optional filter context with role/status/adapter.

        Returns:
            List of matching hooks in priority order.
        """
        ctx = context or {}
        matching: list[RegisteredHook] = []
        for hook in self._hooks.values():
            if not hook.enabled:
                continue
            if event not in hook.events:
                continue
            if hook.hook_filter is not None and not hook.hook_filter.matches(ctx):
                continue
            matching.append(hook)
        matching.sort(key=lambda h: (h.priority, h.name))
        return matching

    async def dispatch(
        self,
        event: HookEvent,
        payload: HookPayload,
        context: dict[str, str] | None = None,
    ) -> list[HookExecutionResult]:
        """Dispatch an event to all matching hooks concurrently.

        Hooks are grouped by priority.  Within each priority tier,
        hooks fire in parallel up to ``max_concurrency``.  Tiers
        execute sequentially from lowest priority number (highest
        priority) to highest.

        Args:
            event: The event to dispatch.
            payload: The payload to pass to each handler.
            context: Optional filter context.

        Returns:
            List of execution results, one per matched hook.
        """
        # Record event for replay
        self._record_event(event, payload, context)

        hooks = self.hooks_for_event(event, context)
        if not hooks:
            return []

        # Group by priority tier
        tiers: dict[int, list[RegisteredHook]] = {}
        for hook in hooks:
            tiers.setdefault(hook.priority, []).append(hook)

        results: list[HookExecutionResult] = []
        sem = asyncio.Semaphore(self._max_concurrency)

        for priority in sorted(tiers):
            tier_hooks = tiers[priority]
            tier_results = await self._run_tier(tier_hooks, event, payload, sem)
            results.extend(tier_results)

        return results

    async def _run_tier(
        self,
        hooks: list[RegisteredHook],
        event: HookEvent,
        payload: HookPayload,
        sem: asyncio.Semaphore,
    ) -> list[HookExecutionResult]:
        """Run a single priority tier of hooks concurrently.

        Args:
            hooks: Hooks in this tier.
            event: The event being dispatched.
            payload: The payload to pass.
            sem: Semaphore to limit concurrency.

        Returns:
            Results for each hook in this tier.
        """

        async def _run_one(hook: RegisteredHook) -> HookExecutionResult:
            async with sem:
                return await self._execute_hook(hook, event, payload)

        tasks = [asyncio.create_task(_run_one(h)) for h in hooks]
        return list(await asyncio.gather(*tasks))

    async def _execute_hook(
        self,
        hook: RegisteredHook,
        event: HookEvent,
        payload: HookPayload,
    ) -> HookExecutionResult:
        """Execute a single hook handler and record metrics.

        Args:
            hook: The hook to execute.
            event: The event being handled.
            payload: The payload.

        Returns:
            Execution result with timing and error info.
        """
        start = time.monotonic()
        try:
            await hook.handler(event, payload)
            duration = time.monotonic() - start
            self._record_metrics(hook.name, duration, success=True)
            return HookExecutionResult(
                hook_name=hook.name,
                success=True,
                duration_s=duration,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            self._record_metrics(hook.name, duration, success=False)
            logger.warning(
                "Hook %r failed for event %s: %s",
                hook.name,
                event.value,
                exc,
            )
            return HookExecutionResult(
                hook_name=hook.name,
                success=False,
                duration_s=duration,
                error=str(exc),
            )

    # -- Metrics (HOOK-009) --

    def _record_metrics(self, hook_name: str, duration_s: float, *, success: bool) -> None:
        """Record metrics for a hook execution."""
        if hook_name not in self._metrics:
            self._metrics[hook_name] = HookMetrics(hook_name=hook_name)
        self._metrics[hook_name].record(duration_s, success=success)

    def get_metrics(self, hook_name: str) -> HookMetrics | None:
        """Return metrics for a specific hook.

        Args:
            hook_name: The hook to look up.

        Returns:
            The metrics, or None if no metrics recorded.
        """
        return self._metrics.get(hook_name)

    def all_metrics(self) -> dict[str, HookMetrics]:
        """Return all hook metrics."""
        return dict(self._metrics)

    def reset_metrics(self) -> None:
        """Clear all recorded metrics."""
        self._metrics.clear()

    # -- Event log for replay (HOOK-011) --

    def _record_event(
        self,
        event: HookEvent,
        payload: HookPayload,
        context: dict[str, str] | None,
    ) -> None:
        """Append an event to the replay log."""
        if len(self._event_log) >= self._max_event_log:
            # Drop oldest 10% to avoid unbounded growth
            drop = self._max_event_log // 10
            self._event_log = self._event_log[drop:]
        self._event_log.append(
            EventRecord(
                event=event,
                payload=payload,
                context=context or {},
            )
        )

    def get_event_log(self, event_filter: HookEvent | None = None) -> list[EventRecord]:
        """Return the event replay log, optionally filtered.

        Args:
            event_filter: If set, only return records for this event.

        Returns:
            List of event records in chronological order.
        """
        if event_filter is None:
            return list(self._event_log)
        return [r for r in self._event_log if r.event == event_filter]

    def clear_event_log(self) -> None:
        """Clear the event replay log."""
        self._event_log.clear()

    async def replay_event(
        self,
        record: EventRecord,
    ) -> list[HookExecutionResult]:
        """Re-dispatch a recorded event through the current hook set.

        Args:
            record: The event record to replay.

        Returns:
            Execution results from the replay.
        """
        return await self.dispatch(
            record.event,
            record.payload,
            record.context or None,
        )
