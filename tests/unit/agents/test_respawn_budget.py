"""Tests for the bounded respawn supervisor (feat/respawn-supervisor-budget).

Covers the four behaviours called out in issue #1629:

* restart within budget recovers without parking;
* budget exhaustion parks the session and publishes the event;
* backoff grows linearly and is capped;
* the rolling window resets so a recovered session regains budget.
"""

from __future__ import annotations

import pytest

from bernstein.core.agents.spawn_supervisor import (
    DEFAULT_INITIAL_BACKOFF_MS,
    DEFAULT_MAX_BACKOFF_MS,
    DEFAULT_MAX_RESPAWNS,
    DEFAULT_WINDOW_SECONDS,
    PARK_REASON_EXHAUSTED,
    RespawnBudget,
    SessionParkedError,
    SpawnSupervisor,
    SupervisorState,
    get_supervisor,
    hook_registry_publisher,
    reset_supervisor,
)


class _FakeClock:
    """Monotonic clock whose value advances only when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _SleepSpy:
    """Captures backoff durations instead of sleeping."""

    def __init__(self, clock: _FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self._clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self._clock is not None:
            self._clock.advance(seconds)


class _Flaky:
    """Spawn callable that fails ``fail_times`` times, then succeeds."""

    def __init__(self, fail_times: int, *, error: str = "boom") -> None:
        self._remaining = fail_times
        self._error = error
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError(self._error)
        return "spawned"


@pytest.fixture(autouse=True)
def _clean_global_supervisor() -> None:
    reset_supervisor()
    yield
    reset_supervisor()


# ---------------------------------------------------------------------------
# RespawnBudget defaults and validation
# ---------------------------------------------------------------------------


def test_budget_defaults_match_acceptance_criteria() -> None:
    budget = RespawnBudget()
    assert budget.max_respawns == DEFAULT_MAX_RESPAWNS == 3
    assert budget.window_seconds == DEFAULT_WINDOW_SECONDS == 60.0
    assert budget.initial_backoff_ms == DEFAULT_INITIAL_BACKOFF_MS == 500
    assert budget.max_backoff_ms == DEFAULT_MAX_BACKOFF_MS == 5000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_respawns": -1},
        {"window_seconds": 0},
        {"initial_backoff_ms": -1},
        {"max_backoff_ms": 100, "initial_backoff_ms": 500},
    ],
)
def test_budget_rejects_invalid_config(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RespawnBudget(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Initial spawn is free
# ---------------------------------------------------------------------------


def test_initial_spawn_succeeds_without_consuming_budget() -> None:
    sup = SpawnSupervisor(sleep=_SleepSpy())
    spawn = _Flaky(fail_times=0)

    result = sup.spawn("s1", spawn)

    assert result.value == "spawned"
    assert result.attempts == 0
    assert result.state == SupervisorState.HEALTHY
    assert sup.respawns_in_window("s1") == 0
    assert spawn.calls == 1


def test_initial_spawn_failure_does_not_count_against_budget() -> None:
    # One failure then success: the failure is a respawn (attempt 1), the
    # initial spawn was the first call. With a budget of 3 we recover.
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=3), sleep=sleep)
    spawn = _Flaky(fail_times=1)

    result = sup.spawn("s1", spawn)

    assert result.value == "spawned"
    assert result.attempts == 1
    assert sup.state("s1") == SupervisorState.HEALTHY


# ---------------------------------------------------------------------------
# Restart within budget
# ---------------------------------------------------------------------------


def test_restart_within_budget_recovers() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=3), sleep=sleep)
    spawn = _Flaky(fail_times=2)

    result = sup.spawn("s1", spawn)

    assert result.value == "spawned"
    assert result.attempts == 2
    assert sup.state("s1") == SupervisorState.HEALTHY
    assert sup.is_parked("s1") is False
    # Two respawns consumed, both still inside the window.
    assert sup.respawns_in_window("s1") == 2


def test_exactly_max_respawns_still_recovers() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=3), sleep=sleep)
    spawn = _Flaky(fail_times=3)

    result = sup.spawn("s1", spawn)

    assert result.attempts == 3
    assert sup.state("s1") == SupervisorState.HEALTHY


# ---------------------------------------------------------------------------
# Exhaustion -> parked + event
# ---------------------------------------------------------------------------


def test_exhaustion_parks_session_and_publishes_event() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    sleep = _SleepSpy()
    sup = SpawnSupervisor(
        RespawnBudget(max_respawns=2),
        publisher=lambda ev, payload: events.append((ev, payload)),
        sleep=sleep,
    )
    spawn = _Flaky(fail_times=99, error="missing binary")

    with pytest.raises(RuntimeError, match="missing binary"):
        sup.spawn("s1", spawn)

    assert sup.state("s1") == SupervisorState.PARKED
    assert sup.is_parked("s1") is True
    assert sup.parked_sessions() == ["s1"]

    assert len(events) == 1
    name, payload = events[0]
    assert name == "agent.startup_exhausted"
    assert payload["reason"] == PARK_REASON_EXHAUSTED
    assert payload["last_error"] == "missing binary"
    assert payload["attempts"] == 2
    assert payload["max_respawns"] == 2
    assert payload["session_id"] == "s1"


def test_parked_session_refuses_further_spawn() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=1), sleep=sleep)

    with pytest.raises(RuntimeError):
        sup.spawn("s1", _Flaky(fail_times=99))
    assert sup.is_parked("s1")

    follow_up = _Flaky(fail_times=0)
    with pytest.raises(SessionParkedError) as excinfo:
        sup.spawn("s1", follow_up)

    # The spawn callable must never run on a parked session.
    assert follow_up.calls == 0
    assert excinfo.value.session_id == "s1"
    assert "resume" in str(excinfo.value)


def test_publisher_failure_does_not_mask_park() -> None:
    def _boom(_ev: str, _payload: dict[str, object]) -> None:
        raise RuntimeError("bus down")

    sup = SpawnSupervisor(RespawnBudget(max_respawns=1), publisher=_boom, sleep=_SleepSpy())

    with pytest.raises(RuntimeError, match="boom"):
        sup.spawn("s1", _Flaky(fail_times=99, error="boom"))
    assert sup.is_parked("s1")


# ---------------------------------------------------------------------------
# Backoff timing
# ---------------------------------------------------------------------------


def test_backoff_grows_linearly() -> None:
    budget = RespawnBudget(initial_backoff_ms=500, max_backoff_ms=5000)
    assert budget.backoff_ms(1) == 500
    assert budget.backoff_ms(2) == 1000
    assert budget.backoff_ms(3) == 1500


def test_backoff_capped_at_max() -> None:
    budget = RespawnBudget(initial_backoff_ms=500, max_backoff_ms=1200)
    assert budget.backoff_ms(3) == 1200  # 1500 clamped to 1200
    assert budget.backoff_ms(50) == 1200


def test_backoff_zero_for_initial_attempt() -> None:
    budget = RespawnBudget(initial_backoff_ms=500)
    assert budget.backoff_ms(0) == 0


def test_supervisor_sleeps_linear_backoff_between_respawns() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(
        RespawnBudget(max_respawns=3, initial_backoff_ms=500, max_backoff_ms=5000),
        sleep=sleep,
    )
    sup.spawn("s1", _Flaky(fail_times=3))

    # Three respawns -> three backoff sleeps, in seconds: 0.5, 1.0, 1.5.
    assert sleep.calls == pytest.approx([0.5, 1.0, 1.5])


# ---------------------------------------------------------------------------
# Window reset
# ---------------------------------------------------------------------------


def test_window_reset_lets_recovered_session_regain_budget() -> None:
    clock = _FakeClock()
    sleep = _SleepSpy(clock)
    sup = SpawnSupervisor(
        RespawnBudget(max_respawns=2, window_seconds=60.0, initial_backoff_ms=0, max_backoff_ms=0),
        sleep=sleep,
        monotonic=clock,
    )

    # Burn the budget down to the edge: two respawns then success.
    sup.spawn("s1", _Flaky(fail_times=2))
    assert sup.respawns_in_window("s1") == 2

    # Stay healthy long enough for the window to age out both respawns.
    clock.advance(61.0)
    assert sup.respawns_in_window("s1") == 0

    # Full budget is available again: two fresh respawns recover, no park.
    result = sup.spawn("s1", _Flaky(fail_times=2))
    assert result.attempts == 2
    assert sup.state("s1") == SupervisorState.HEALTHY


def test_respawns_outside_window_do_not_trigger_park() -> None:
    clock = _FakeClock()
    sup = SpawnSupervisor(
        RespawnBudget(max_respawns=1, window_seconds=10.0, initial_backoff_ms=0, max_backoff_ms=0),
        sleep=_SleepSpy(),
        monotonic=clock,
    )

    # First call: one respawn then success (budget of 1, recovers).
    sup.spawn("s1", _Flaky(fail_times=1))
    assert sup.respawns_in_window("s1") == 1

    clock.advance(11.0)  # Age the single respawn out of the window.
    result = sup.spawn("s1", _Flaky(fail_times=1))
    assert result.value == "spawned"
    assert sup.is_parked("s1") is False


# ---------------------------------------------------------------------------
# Resume semantics
# ---------------------------------------------------------------------------


def test_resume_clears_parked_state_and_resets_budget() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=1), sleep=sleep)

    with pytest.raises(RuntimeError):
        sup.spawn("s1", _Flaky(fail_times=99))
    assert sup.is_parked("s1")

    assert sup.resume("s1") is True
    assert sup.state("s1") == SupervisorState.HEALTHY
    assert sup.respawns_in_window("s1") == 0
    assert sup.parked_sessions() == []

    # After resume the session can spawn again.
    result = sup.spawn("s1", _Flaky(fail_times=0))
    assert result.value == "spawned"


def test_resume_unknown_session_returns_false() -> None:
    sup = SpawnSupervisor()
    assert sup.resume("nope") is False


def test_resume_grants_a_fresh_full_budget() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=2), sleep=sleep)

    with pytest.raises(RuntimeError):
        sup.spawn("s1", _Flaky(fail_times=99))
    assert sup.is_parked("s1")

    sup.resume("s1")
    # Fresh budget of 2 respawns recovers without re-parking.
    result = sup.spawn("s1", _Flaky(fail_times=2))
    assert result.attempts == 2
    assert sup.is_parked("s1") is False


# ---------------------------------------------------------------------------
# Process-wide supervisor (shared by orchestrator and CLI resume)
# ---------------------------------------------------------------------------


def test_get_supervisor_is_process_singleton() -> None:
    assert get_supervisor() is get_supervisor()


def test_cli_resume_path_reaches_orchestrator_budget() -> None:
    # The orchestrator parks via the global supervisor; the CLI resume
    # command reads the same instance, so resume must reach the budget.
    sup = get_supervisor()
    sup._default_budget = RespawnBudget(max_respawns=1)  # test wiring
    sup._sleep = _SleepSpy()  # test wiring

    with pytest.raises(RuntimeError):
        sup.spawn("worker-3", _Flaky(fail_times=99))
    assert "worker-3" in get_supervisor().parked_sessions()

    assert get_supervisor().resume("worker-3") is True
    assert get_supervisor().parked_sessions() == []


# ---------------------------------------------------------------------------
# Lifecycle-bus adapter
# ---------------------------------------------------------------------------


def test_hook_registry_publisher_dispatches_exhaustion_event() -> None:
    from bernstein.core.lifecycle.hooks import (
        HookRegistry,
        LifecycleContext,
        LifecycleEvent,
    )

    seen: list[LifecycleContext] = []
    registry = HookRegistry()
    registry.register_callable(LifecycleEvent.AGENT_STARTUP_EXHAUSTED, seen.append)

    sup = SpawnSupervisor(
        RespawnBudget(max_respawns=1),
        publisher=hook_registry_publisher(registry),
        sleep=_SleepSpy(),
    )

    with pytest.raises(RuntimeError):
        sup.spawn("s1", _Flaky(fail_times=99, error="expired token"))

    assert len(seen) == 1
    ctx = seen[0]
    assert ctx.event == LifecycleEvent.AGENT_STARTUP_EXHAUSTED
    assert ctx.session_id == "s1"
    assert ctx.data["reason"] == PARK_REASON_EXHAUSTED
    assert ctx.data["last_error"] == "expired token"


# ---------------------------------------------------------------------------
# Multi-session isolation
# ---------------------------------------------------------------------------


def test_sessions_have_independent_budgets() -> None:
    sleep = _SleepSpy()
    sup = SpawnSupervisor(RespawnBudget(max_respawns=1), sleep=sleep)

    with pytest.raises(RuntimeError):
        sup.spawn("doomed", _Flaky(fail_times=99))
    assert sup.is_parked("doomed")

    # A different session is unaffected and spawns cleanly.
    result = sup.spawn("healthy", _Flaky(fail_times=0))
    assert result.value == "spawned"
    assert sup.is_parked("healthy") is False
