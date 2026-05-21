"""Tests for the opt-in LLM watcher (observer above the deterministic loop).

These tests cover the read-only contract spelled out in the watcher
ticket and module docstring:

* (a) Watcher disabled  -> zero LLM calls.
* (b) Watcher enabled   -> events processed and suggestions produced.
* (c) Watcher read-only -> the API surface offers no path to mutate
      orchestrator state (no setter, no callable in WatcherEvent, etc.).
* (d) Watcher failure   -> exceptions inside the LLM caller surface as
      empty signal lists; the orchestrator is never crashed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from collections.abc import Awaitable
from typing import Any

import pytest

from bernstein.core.observability.llm_watcher import (
    LLMWatcher,
    Suggestion,
    WatcherConfig,
    WatcherEvent,
    build_watcher_from_env,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(kind: str = "task_spawned") -> WatcherEvent:
    """Build a deterministic WatcherEvent for tests."""
    return WatcherEvent(
        kind=kind,  # type: ignore[arg-type]  # Literal narrowing
        run_id="run-test-001",
        timestamp=1_715_000_000.0,
        payload={"task_id": "T-1", "tick": 7},
    )


class _RecordingCaller:
    """Async callable stub that records invocations and returns canned text."""

    def __init__(self, response: str = "anomaly: same tool called 4 times") -> None:
        self.response = response
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        return self.response


class _ExplodingCaller:
    """Async callable stub that always raises."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        msg = "watcher LLM unavailable"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# (a) Disabled by default -> zero LLM calls
# ---------------------------------------------------------------------------


class TestDisabledByDefault:
    """The watcher must be off by default and make zero LLM calls."""

    def test_default_config_disabled(self) -> None:
        cfg = WatcherConfig()
        assert cfg.enabled is False

    def test_build_from_env_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Be defensive: clear any leaked env var so the test is hermetic.
        monkeypatch.delenv("BERNSTEIN_LLM_WATCHER_ENABLED", raising=False)
        watcher = build_watcher_from_env()
        assert watcher.config.enabled is False

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "FALSE", "  "])
    def test_env_var_falsey_values_keep_watcher_off(
        self,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv("BERNSTEIN_LLM_WATCHER_ENABLED", value)
        watcher = build_watcher_from_env()
        assert watcher.config.enabled is False

    def test_disabled_observe_returns_empty(self) -> None:
        caller = _RecordingCaller()
        watcher = LLMWatcher(WatcherConfig(enabled=False), llm_caller=caller)
        result = asyncio.run(watcher.observe(_make_event()))
        assert result == []
        assert caller.calls == [], "Disabled watcher must NOT invoke the LLM"
        assert watcher.call_count == 0

    def test_disabled_no_default_caller_import(self) -> None:
        """Disabled watcher must not import the project LLM stack.

        The watcher's *only* lazy import lives in ``_default_llm_caller``.
        We pass ``llm_caller=None`` and confirm a disabled watcher never
        triggers it.  If it did, the test would still pass because the
        import is harmless - but the recorded call count would jump.
        """
        watcher = LLMWatcher(WatcherConfig(enabled=False), llm_caller=None)
        result = asyncio.run(watcher.observe(_make_event()))
        assert result == []
        assert watcher.call_count == 0


# ---------------------------------------------------------------------------
# (b) Enabled -> events processed, suggestions produced
# ---------------------------------------------------------------------------


class TestEnabledProcessesEvents:
    """Enabled watcher must call the LLM and produce suggestions."""

    def test_enabled_emits_suggestion(self) -> None:
        caller = _RecordingCaller(response="watch out: stuck loop on T-1")
        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        signals = asyncio.run(watcher.observe(_make_event("task_spawned")))
        assert len(signals) == 1
        sig = signals[0]
        assert isinstance(sig, Suggestion)
        assert sig.run_id == "run-test-001"
        assert "stuck loop" in sig.rationale
        assert sig.detector == "observer"
        assert sig.severity == "info"
        assert watcher.call_count == 1
        assert watcher.suggestion_count == 1

    def test_enabled_passes_model_and_provider(self) -> None:
        caller = _RecordingCaller()
        cfg = WatcherConfig(enabled=True, model="haiku", provider="claude")
        watcher = LLMWatcher(cfg, llm_caller=caller)
        asyncio.run(watcher.observe(_make_event()))
        assert len(caller.calls) == 1
        _, kwargs = caller.calls[0]
        assert kwargs["provider"] == "claude"
        # The model is passed as the second positional argument.
        args, _ = caller.calls[0]
        assert args[1] == "haiku"

    def test_enabled_blank_response_yields_no_signal(self) -> None:
        caller = _RecordingCaller(response="   \n   ")
        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        signals = asyncio.run(watcher.observe(_make_event()))
        assert signals == []
        assert watcher.suggestion_count == 0

    @pytest.mark.parametrize(
        "kind",
        ["plan_decided", "task_spawned", "task_completed", "merge_decided"],
    )
    def test_all_event_kinds_accepted(self, kind: str) -> None:
        caller = _RecordingCaller(response="note")
        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        signals = asyncio.run(watcher.observe(_make_event(kind)))
        assert len(signals) == 1


# ---------------------------------------------------------------------------
# (c) Read-only contract: structural enforcement, not just hope
# ---------------------------------------------------------------------------


class TestReadOnlyContract:
    """The watcher API must offer no path to mutate orchestrator state."""

    def test_watcher_event_is_frozen(self) -> None:
        ev = _make_event()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.run_id = "tampered"  # type: ignore[misc]

    def test_suggestion_is_frozen(self) -> None:
        sig = Suggestion(
            suggestion_id="s",
            run_id="r",
            detector="observer",
            severity="info",
            rationale="x",
            proposed_action="y",
            cost_usd=0.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            sig.severity = "critical"  # type: ignore[misc]

    def test_watcher_config_is_frozen(self) -> None:
        cfg = WatcherConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.enabled = True  # type: ignore[misc]

    def test_watcher_constructor_takes_no_orchestrator_handle(self) -> None:
        """LLMWatcher.__init__ must not accept any orchestrator-level handle.

        The read-only enforcement is structural: the watcher cannot
        spawn agents, write to the task store, or edit files because
        no such handle is in its constructor signature.
        """
        sig = inspect.signature(LLMWatcher.__init__)
        forbidden = {
            "orchestrator",
            "task_store",
            "spawner",
            "workdir",
            "filesystem",
            "agent_registry",
        }
        assert forbidden.isdisjoint(sig.parameters), (
            f"LLMWatcher must not accept any of {forbidden}; got {set(sig.parameters)}"
        )

    def test_watcher_event_payload_does_not_carry_callables(self) -> None:
        """The event payload is intended to be JSON-shaped, not callable.

        Even if a caller stuffs a callable in, the watcher does not
        execute it - there is no ``payload[...](...)`` call site.  We
        assert behaviourally: when given a callable in the payload, the
        watcher does not invoke it and still degrades cleanly.
        """
        invoked = {"count": 0}

        def naughty() -> str:
            invoked["count"] += 1
            return "tampered"

        # We construct the event with a callable to prove the watcher
        # does not reach into it.  ``observe`` should still complete.
        ev = WatcherEvent(
            kind="task_spawned",
            run_id="run-test-001",
            timestamp=1.0,
            payload={"naughty": naughty},  # type: ignore[dict-item]
        )
        caller = _RecordingCaller()
        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        signals = asyncio.run(watcher.observe(ev))
        # Watcher returns one signal (the canned response) but the
        # callable in payload was never invoked.
        assert len(signals) == 1
        assert invoked["count"] == 0

    def test_module_does_not_import_task_store(self) -> None:
        """The watcher module must not import any state-mutating subsystem.

        We inspect the module source statically.  This is a guard
        against future refactors silently wiring in task / agent /
        filesystem handles.
        """
        from bernstein.core.observability import llm_watcher as mod

        source = inspect.getsource(mod)
        forbidden_imports = (
            "from bernstein.core.tasks",
            "from bernstein.core.agents",
            "from bernstein.core.git",
            "from bernstein.core.persistence",
        )
        for needle in forbidden_imports:
            assert needle not in source, (
                f"Watcher module must not import {needle!r}; would break the read-only contract."
            )


# ---------------------------------------------------------------------------
# (d) Watcher failures don't crash the orchestrator
# ---------------------------------------------------------------------------


class TestWatcherFailuresAreSafe:
    """A misbehaving watcher must NOT crash the orchestrator."""

    def test_llm_caller_raises_returns_empty_signals(self) -> None:
        caller = _ExplodingCaller()
        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        signals = asyncio.run(watcher.observe(_make_event()))
        assert signals == []
        assert caller.calls == 1, "Watcher should have attempted exactly one LLM call"

    def test_observe_never_raises(self) -> None:
        async def boom(*_a: Any, **_kw: Any) -> str:
            raise ValueError("boom")

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=boom)
        # Should NOT raise.
        result = asyncio.run(watcher.observe(_make_event()))
        assert result == []

    def test_observe_does_not_propagate_keyboard_interrupt_handling(self) -> None:
        """KeyboardInterrupt is not a regular Exception, so it bubbles.

        We document the expected escape hatch: BaseException is not
        caught.  A failing LLM is bounded (Exception); shutdown
        signals are not - they should still propagate so operators can
        Ctrl-C cleanly.
        """

        async def cancel(*_a: Any, **_kw: Any) -> str:
            raise KeyboardInterrupt

        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=cancel)
        with pytest.raises(KeyboardInterrupt):
            asyncio.run(watcher.observe(_make_event()))


# ---------------------------------------------------------------------------
# Sanity: build_watcher_from_env when explicitly enabled
# ---------------------------------------------------------------------------


class TestBuildFromEnv:
    """``build_watcher_from_env`` reads three env vars."""

    def test_truthy_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_LLM_WATCHER_ENABLED", "1")
        watcher = build_watcher_from_env()
        assert watcher.config.enabled is True

    def test_model_and_provider_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BERNSTEIN_LLM_WATCHER_ENABLED", "true")
        monkeypatch.setenv("BERNSTEIN_LLM_WATCHER_MODEL", "sonnet")
        monkeypatch.setenv("BERNSTEIN_LLM_WATCHER_PROVIDER", "openrouter_free")
        watcher = build_watcher_from_env()
        assert watcher.config.model == "sonnet"
        assert watcher.config.provider == "openrouter_free"

    def test_caller_injection_is_used(self) -> None:
        """The injection seam allows tests to bypass the lazy LLM import."""
        caller = _RecordingCaller()
        # Build manually because env build always uses lazy default.
        watcher = LLMWatcher(WatcherConfig(enabled=True), llm_caller=caller)
        asyncio.run(watcher.observe(_make_event()))
        assert len(caller.calls) == 1


# ---------------------------------------------------------------------------
# (b extension) End-to-end observe() shape - keeps the module honest about
# its public contract.
# ---------------------------------------------------------------------------


class TestSuggestionShape:
    """Smoke check on the suggestion fields.  This test is the canonical
    consumer of ``Suggestion`` and so guards against silent renames.
    """

    def test_observe_signature_is_async(self) -> None:
        assert inspect.iscoroutinefunction(LLMWatcher.observe)

    def test_observe_returns_awaitable(self) -> None:
        watcher = LLMWatcher(WatcherConfig(enabled=False))
        coro = watcher.observe(_make_event())
        assert isinstance(coro, Awaitable)
        # Drain to avoid "never awaited" warnings.
        asyncio.run(coro)  # type: ignore[arg-type]
