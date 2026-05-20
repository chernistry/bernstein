"""Bounded respawn supervisor with park-on-exhaustion.

A naive retry loop on adapter spawn failure produces tight crash loops
that mask the underlying fault (bad config, missing binary, expired
token) under noise. Giving up on the first failure, conversely, wastes
operator attention on transient flakes.

This module gives every supervised session a documented respawn budget:

* The initial spawn does not count against the budget.
* Up to ``max_respawns`` respawns are permitted inside a rolling
  ``window_seconds`` window.
* Each respawn waits a linearly growing backoff
  (``initial_backoff_ms * attempt``) capped at ``max_backoff_ms``.
* When the budget is exhausted the session transitions to ``parked`` and
  a single :data:`LifecycleEvent.AGENT_STARTUP_EXHAUSTED` event is
  published through the lifecycle bus.
* An operator resets the budget explicitly (``bernstein agents resume
  <id>``); there is no automatic remediation.

The supervisor is deliberately transport-agnostic: callers supply a
spawn callable and the supervisor owns only the budget accounting,
backoff schedule, parking, and event publication. This keeps it usable
standalone in tests without dragging in the full spawner.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

#: Default maximum respawns inside the rolling window (initial spawn excluded).
DEFAULT_MAX_RESPAWNS: int = 3

#: Default rolling window in seconds.
DEFAULT_WINDOW_SECONDS: float = 60.0

#: Default backoff for the first respawn, in milliseconds.
DEFAULT_INITIAL_BACKOFF_MS: int = 500

#: Default ceiling on the linear backoff, in milliseconds.
DEFAULT_MAX_BACKOFF_MS: int = 5000

#: Machine-readable park reason emitted on the lifecycle bus.
PARK_REASON_EXHAUSTED: str = "respawn_budget_exhausted"


class SupervisorState(StrEnum):
    """Lifecycle state of a supervised session.

    ``HEALTHY`` is the steady state once an initial spawn succeeds.
    ``RESPAWNING`` is transient while backoff is in effect.
    ``PARKED`` is terminal until an operator resumes the session; the
    supervisor refuses to spawn a parked session.
    """

    HEALTHY = "healthy"
    RESPAWNING = "respawning"
    PARKED = "parked"


class SessionParkedError(RuntimeError):
    """Raised when a spawn is attempted on a parked session.

    Attributes:
        session_id: The parked session identifier.
        attempts: Number of respawn attempts that were consumed.
        last_error: Stringified final spawn error, or empty string.
    """

    def __init__(self, session_id: str, attempts: int, last_error: str) -> None:
        self.session_id = session_id
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Session '{session_id}' is parked after {attempts} exhausted respawn(s). "
            f"Resume it with 'bernstein agents resume {session_id}'."
        )


@dataclass(frozen=True)
class RespawnBudget:
    """Bounded respawn policy for a supervised session.

    Attributes:
        max_respawns: Maximum respawns permitted inside ``window_seconds``.
            The initial spawn is never counted against this ceiling.
        window_seconds: Length of the rolling window. Respawn timestamps
            older than this fall out of the count, so a session that
            recovers and stays up long enough regains its full budget.
        initial_backoff_ms: Backoff applied before the first respawn.
        max_backoff_ms: Upper bound on the linearly growing backoff.
    """

    max_respawns: int = DEFAULT_MAX_RESPAWNS
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    initial_backoff_ms: int = DEFAULT_INITIAL_BACKOFF_MS
    max_backoff_ms: int = DEFAULT_MAX_BACKOFF_MS

    def __post_init__(self) -> None:
        if self.max_respawns < 0:
            raise ValueError("max_respawns must be >= 0")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.initial_backoff_ms < 0:
            raise ValueError("initial_backoff_ms must be >= 0")
        if self.max_backoff_ms < self.initial_backoff_ms:
            raise ValueError("max_backoff_ms must be >= initial_backoff_ms")

    def backoff_ms(self, attempt: int) -> int:
        """Return the backoff in milliseconds before the ``attempt``-th respawn.

        Backoff grows linearly with the respawn attempt number and is
        capped at :attr:`max_backoff_ms`.

        Args:
            attempt: 1-indexed respawn attempt number.

        Returns:
            Backoff in milliseconds, clamped to ``[0, max_backoff_ms]``.
        """
        if attempt < 1:
            return 0
        return min(self.initial_backoff_ms * attempt, self.max_backoff_ms)


@dataclass
class _SessionRecord:
    """Mutable per-session supervision bookkeeping.

    Attributes:
        budget: The respawn budget governing this session.
        state: Current supervisor state.
        respawn_times: Monotonic timestamps of respawns inside the window.
        total_respawns: Lifetime respawn counter (never pruned), for telemetry.
        last_error: Stringified final spawn error, or empty string.
    """

    budget: RespawnBudget
    state: SupervisorState = SupervisorState.HEALTHY
    respawn_times: list[float] = field(default_factory=list[float])
    total_respawns: int = 0
    last_error: str = ""


@dataclass(frozen=True)
class SupervisedSpawn[T]:
    """Outcome of a supervised spawn attempt.

    Attributes:
        value: The spawn callable's return value on success.
        attempts: Number of respawn attempts consumed for this call
            (0 when the initial spawn succeeded immediately).
        state: Supervisor state after the call.
    """

    value: T
    attempts: int
    state: SupervisorState


#: A bus publisher: receives the event name and a payload mapping. Kept
#: deliberately loose so callers can pass a ``HookRegistry``-backed
#: adapter, a test spy, or a plain logging sink.
BusPublisher = Callable[[str, dict[str, Any]], None]


def _default_publisher(event: str, payload: dict[str, Any]) -> None:
    """Fallback publisher used when no lifecycle bus is wired.

    Logs the exhaustion at WARNING so the park is never silent even in
    standalone use.
    """
    logger.warning("lifecycle event %s: %s", event, payload)


class SpawnSupervisor:
    """Supervises bounded respawns and parks sessions on exhaustion.

    Thread-safe. One supervisor instance may manage many sessions, each
    keyed by an opaque session id and governed by its own
    :class:`RespawnBudget`. Backoff sleeps are delegated to an injectable
    ``sleep`` callable so tests can assert timing without real waits.

    Args:
        budget: Default budget applied to sessions that do not supply
            their own at :meth:`spawn` time.
        publisher: Lifecycle bus publisher invoked once on park. When
            None, a logging fallback is used.
        sleep: Backoff sleep function. Defaults to :func:`time.sleep`.
        monotonic: Monotonic clock. Defaults to :func:`time.monotonic`.
    """

    def __init__(
        self,
        budget: RespawnBudget | None = None,
        *,
        publisher: BusPublisher | None = None,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._default_budget = budget or RespawnBudget()
        self._publisher = publisher or _default_publisher
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionRecord] = {}

    # ------------------------------------------------------------------ queries

    def state(self, session_id: str) -> SupervisorState:
        """Return the current state of ``session_id`` (HEALTHY if unknown)."""
        with self._lock:
            record = self._sessions.get(session_id)
            return record.state if record is not None else SupervisorState.HEALTHY

    def is_parked(self, session_id: str) -> bool:
        """Return True when ``session_id`` is parked."""
        return self.state(session_id) == SupervisorState.PARKED

    def parked_sessions(self) -> list[str]:
        """Return the ids of all currently parked sessions, sorted."""
        with self._lock:
            return sorted(sid for sid, rec in self._sessions.items() if rec.state == SupervisorState.PARKED)

    def respawns_in_window(self, session_id: str) -> int:
        """Return the number of respawns still inside the rolling window."""
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return 0
            self._prune(record, self._monotonic())
            return len(record.respawn_times)

    # ------------------------------------------------------------------ control

    def resume(self, session_id: str) -> bool:
        """Reset the budget for ``session_id`` and clear its parked state.

        This is the operator-driven recovery path. Resuming a session
        that is not parked is a no-op that still clears its respawn
        window, so it is safe to call defensively.

        Args:
            session_id: Session to resume.

        Returns:
            True if a tracked session was reset, False if it was unknown.
        """
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return False
            was_parked = record.state == SupervisorState.PARKED
            record.respawn_times.clear()
            record.state = SupervisorState.HEALTHY
            record.last_error = ""
        if was_parked:
            logger.info("Resumed parked session '%s'; respawn budget reset", session_id)
        return True

    def forget(self, session_id: str) -> None:
        """Drop all supervision state for ``session_id``."""
        with self._lock:
            self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------ spawn

    def spawn[T](
        self,
        session_id: str,
        spawn_fn: Callable[[], T],
        *,
        budget: RespawnBudget | None = None,
    ) -> SupervisedSpawn[T]:
        """Spawn ``session_id`` under the respawn budget, retrying on failure.

        The first call's initial spawn does not consume budget. Each
        subsequent failure inside the same call consumes one respawn,
        sleeps the linear backoff, and retries until either the spawn
        succeeds or the budget is exhausted. On exhaustion the session is
        parked, an :data:`LifecycleEvent.AGENT_STARTUP_EXHAUSTED` event is
        published, and the originating error is raised.

        Calling :meth:`spawn` on an already-parked session never invokes
        ``spawn_fn`` and raises :class:`SessionParkedError` immediately.

        Args:
            session_id: Opaque session identifier.
            spawn_fn: Zero-argument callable that performs one spawn
                attempt. Raises on failure; its return value is surfaced
                in :class:`SupervisedSpawn` on success.
            budget: Per-call budget override. When None the supervisor's
                default budget is used (and pinned on first sight of the
                session).

        Returns:
            A :class:`SupervisedSpawn` describing the successful outcome.

        Raises:
            SessionParkedError: If the session was already parked.
            Exception: The final spawn error, re-raised after parking.
        """
        record = self._record_for(session_id, budget)

        if record.state == SupervisorState.PARKED:
            raise SessionParkedError(session_id, record.total_respawns, record.last_error)

        attempts_this_call = 0
        while True:
            try:
                value = spawn_fn()
            except Exception as exc:  # we account for the failure, then re-raise
                if not self._consume_respawn(record, exc):
                    self._park(session_id, record, attempts_this_call)
                    raise
                attempts_this_call += 1
                self._sleep(record.budget.backoff_ms(attempts_this_call) / 1000.0)
                continue

            # A successful spawn cannot reach here while parked: parking
            # always raises out of the loop above. Mark the session healthy.
            with self._lock:
                record.state = SupervisorState.HEALTHY
            return SupervisedSpawn(value=value, attempts=attempts_this_call, state=SupervisorState.HEALTHY)

    # ------------------------------------------------------------------ internals

    def _record_for(self, session_id: str, budget: RespawnBudget | None) -> _SessionRecord:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                record = _SessionRecord(budget=budget or self._default_budget)
                self._sessions[session_id] = record
            return record

    def _prune(self, record: _SessionRecord, now: float) -> None:
        cutoff = now - record.budget.window_seconds
        record.respawn_times = [t for t in record.respawn_times if t > cutoff]

    def _consume_respawn(self, record: _SessionRecord, exc: Exception) -> bool:
        """Record a respawn attempt; return False when the budget is spent."""
        with self._lock:
            now = self._monotonic()
            self._prune(record, now)
            record.last_error = str(exc)
            if len(record.respawn_times) >= record.budget.max_respawns:
                return False
            record.respawn_times.append(now)
            record.total_respawns += 1
            record.state = SupervisorState.RESPAWNING
            return True

    def _park(self, session_id: str, record: _SessionRecord, attempts: int) -> None:
        with self._lock:
            record.state = SupervisorState.PARKED
            last_error = record.last_error
            budget = record.budget
        logger.error(
            "Session '%s' parked after exhausting respawn budget (%d respawn(s) in %.0fs window); last error: %s",
            session_id,
            attempts,
            budget.window_seconds,
            last_error or "<none>",
        )
        self._publish_exhausted(session_id, attempts, last_error, budget)

    def _publish_exhausted(
        self,
        session_id: str,
        attempts: int,
        last_error: str,
        budget: RespawnBudget,
    ) -> None:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "reason": PARK_REASON_EXHAUSTED,
            "last_error": last_error,
            "attempts": attempts,
            "window_seconds": budget.window_seconds,
            "max_respawns": budget.max_respawns,
        }
        try:
            self._publisher("agent.startup_exhausted", payload)
        except Exception:  # publication must never mask the park
            logger.exception("Failed to publish AgentStartupExhausted for session '%s'", session_id)


# ---------------------------------------------------------------------------
# Lifecycle-bus adapter
# ---------------------------------------------------------------------------


def hook_registry_publisher(registry: Any) -> BusPublisher:
    """Build a :data:`BusPublisher` that fans events into a ``HookRegistry``.

    The supervisor stays decoupled from the lifecycle package; callers
    that already own a :class:`~bernstein.core.lifecycle.hooks.HookRegistry`
    wrap it with this adapter so park events reach registered hooks.

    Args:
        registry: A ``HookRegistry`` exposing ``run(event, context)``.

    Returns:
        A publisher suitable for :class:`SpawnSupervisor`.
    """

    def _publish(event: str, payload: dict[str, Any]) -> None:
        from bernstein.core.lifecycle.hooks import LifecycleContext, LifecycleEvent

        ctx = LifecycleContext(
            event=LifecycleEvent.AGENT_STARTUP_EXHAUSTED,
            session_id=payload.get("session_id"),
            data=payload.copy(),
        )
        registry.run(LifecycleEvent.AGENT_STARTUP_EXHAUSTED, ctx)

    return _publish


# ---------------------------------------------------------------------------
# Process-scoped registry
# ---------------------------------------------------------------------------

_global_supervisor: SpawnSupervisor | None = None
_global_lock = threading.Lock()


def get_supervisor() -> SpawnSupervisor:
    """Return the process-wide supervisor, creating it on first use.

    The orchestrator and the CLI ``resume`` command share this instance
    so an operator's resume reaches the same budget the spawner consults.
    """
    global _global_supervisor
    with _global_lock:
        if _global_supervisor is None:
            _global_supervisor = SpawnSupervisor()
        return _global_supervisor


def reset_supervisor() -> None:
    """Drop the process-wide supervisor (test hook)."""
    global _global_supervisor
    with _global_lock:
        _global_supervisor = None
