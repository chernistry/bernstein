"""Tests for the orchestrator-side LLM watcher dispatch hook.

These tests use a thin stand-in for ``Orchestrator`` that exercises
just the ``_dispatch_watcher_events`` code path on a fake watcher.
The full ``Orchestrator.__init__`` requires httpx, an
:class:`AgentSpawner`, and a workdir with seed config - too heavy
for a focused unit test.  Spinning up the full object is exercised
elsewhere in :mod:`tests.unit.test_orchestrator`.

What we cover here:

1. The dispatcher is a no-op when the watcher is disabled (no LLM
   calls, no signals collected).
2. The dispatcher routes ``spawned`` and ``verified`` task ids into
   ``task_spawned`` and ``task_completed`` events respectively.
3. The dispatcher swallows watcher exceptions and never raises into
   the orchestrator tick.
"""

from __future__ import annotations

import collections
from typing import Any

import pytest

from bernstein.core.observability.llm_watcher import (
    LLMWatcher,
    Suggestion,
    WatcherConfig,
    WatcherEvent,
)
from bernstein.core.orchestration.orchestrator import Orchestrator, TickResult


class _FakeOrchestrator:
    """Minimal stand-in carrying just the state ``_dispatch_watcher_events`` reads.

    We avoid building a real ``Orchestrator`` because its constructor is
    heavyweight (httpx client, spawner, file locks, telemetry, etc.).
    The dispatcher only touches a handful of attributes.
    """

    def __init__(self, watcher: LLMWatcher) -> None:
        self._llm_watcher = watcher
        self._llm_watcher_signals: collections.deque[Any] = collections.deque(maxlen=64)
        self._tick_count = 7
        self._run_id = "run-test"

    # Bind the dispatcher method off the real Orchestrator class so
    # behaviour stays in lockstep with production.
    _dispatch_watcher_events = Orchestrator._dispatch_watcher_events  # type: ignore[assignment]


class _ExplodingWatcher(LLMWatcher):
    """Watcher whose ``observe`` actively raises - used for the (d) case.

    We bypass the normal ``observe`` body to confirm that even an
    LLMWatcher subclass that breaks the contract cannot crash the
    orchestrator dispatcher.
    """

    def __init__(self) -> None:
        super().__init__(WatcherConfig(enabled=True))

    async def observe(self, event: WatcherEvent) -> list[Suggestion]:  # type: ignore[override]
        msg = "watcher subclass exploded"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Disabled => zero LLM calls
# ---------------------------------------------------------------------------


class TestDispatcherDisabled:
    """When the watcher is off the dispatcher must short-circuit."""

    def test_disabled_no_signals(self) -> None:
        calls: list[Any] = []

        async def caller(*args: Any, **kwargs: Any) -> str:
            calls.append((args, kwargs))
            return "should not happen"

        watcher = LLMWatcher(WatcherConfig(enabled=False), llm_caller=caller)
        orch = _FakeOrchestrator(watcher)

        result = TickResult()
        result.spawned = ["t-1", "t-2"]
        result.verified = ["t-3"]

        # MUST NOT raise.
        orch._dispatch_watcher_events(result)

        assert calls == []
        assert list(orch._llm_watcher_signals) == []

    def test_disabled_no_op_on_empty_result(self) -> None:
        watcher = LLMWatcher(WatcherConfig(enabled=False))
        orch = _FakeOrchestrator(watcher)
        orch._dispatch_watcher_events(TickResult())
        assert list(orch._llm_watcher_signals) == []


# ---------------------------------------------------------------------------
# Enabled => events produced and routed correctly
# ---------------------------------------------------------------------------


class TestDispatcherEnabled:
    """When the watcher is on, spawned/verified ids are translated to events."""

    def test_emits_task_spawned_and_completed_events(self) -> None:
        seen_kinds: list[str] = []

        async def caller(*_a: Any, **_kw: Any) -> str:
            # We hijack the LLM call to record what kind the watcher saw.
            # The most reliable signal is the event kind which appears in
            # the prompt body.
            prompt = _a[0]
            for kind in ("task_spawned", "task_completed", "plan_decided", "merge_decided"):
                if f"Event kind: {kind}" in prompt:
                    seen_kinds.append(kind)
                    break
            return "anomaly noted"

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        orch = _FakeOrchestrator(watcher)

        result = TickResult()
        result.spawned = ["t-spawn-1", "t-spawn-2"]
        result.verified = ["t-done-1"]

        orch._dispatch_watcher_events(result)

        # 2 spawned + 1 verified = 3 LLM calls and 3 suggestions.
        assert seen_kinds == ["task_spawned", "task_spawned", "task_completed"]
        signals = list(orch._llm_watcher_signals)
        assert len(signals) == 3
        for sig in signals:
            assert isinstance(sig, Suggestion)
            assert sig.run_id == "run-test"
            assert sig.detector == "observer"

    def test_no_events_when_result_is_empty(self) -> None:
        async def caller(*_a: Any, **_kw: Any) -> str:
            pytest.fail("LLM should not be called when nothing happened this tick")

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        orch = _FakeOrchestrator(watcher)
        orch._dispatch_watcher_events(TickResult())
        assert list(orch._llm_watcher_signals) == []


# ---------------------------------------------------------------------------
# Failures inside the watcher do not propagate
# ---------------------------------------------------------------------------


class TestDispatcherFailureSafety:
    """A misbehaving watcher must not crash the orchestrator tick."""

    def test_watcher_observe_raises_dispatcher_swallows(self) -> None:
        watcher = _ExplodingWatcher()
        orch = _FakeOrchestrator(watcher)

        result = TickResult()
        result.spawned = ["t-1"]

        # MUST NOT raise - orchestrator stability is the contract.
        orch._dispatch_watcher_events(result)

        # Nothing collected because every observe() failed.
        assert list(orch._llm_watcher_signals) == []

    def test_caller_raises_dispatcher_swallows(self) -> None:
        async def caller(*_a: Any, **_kw: Any) -> str:
            raise RuntimeError("LLM endpoint down")

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        orch = _FakeOrchestrator(watcher)

        result = TickResult()
        result.verified = ["t-1", "t-2"]

        # MUST NOT raise.
        orch._dispatch_watcher_events(result)

        assert list(orch._llm_watcher_signals) == []
