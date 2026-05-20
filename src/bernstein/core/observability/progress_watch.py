"""ProgressWatch: liveness probe based on session-log file growth.

When Bernstein spawns a CLI agent subprocess, the only reliable way to
know whether the agent is "stuck or just thinking" is to ignore its own
stdout (which it may buffer, or pause for tool calls) and watch a
structured session log on disk. Most cooperating CLIs append to such a
log as they make progress; an idle log file means the agent has stopped
making progress.

The class is deliberately small and single-threaded:

* :meth:`register` records a (session_id, log_path) pair.
* :meth:`tick` samples the current mtime/size of every registered log
  and decides which sessions have crossed the inactivity / kill
  thresholds.
* :meth:`kill_if_stale` returns the kill verdict for a single session,
  suitable for an external dispatch loop that owns the process handle.

The watcher does not spawn its own thread and does not kill processes
directly. The dispatch loop is responsible for both:

* calling :meth:`tick` on a cadence (e.g. every 30s) and
* turning a :class:`KillVerdict` into the actual SIGTERM/SIGKILL.

This separation keeps the watcher trivially testable (inject a clock,
inject a stat function) and avoids embedding lifecycle policy in an
observability primitive.

Lifecycle integration
---------------------

When the inactivity threshold is crossed, the watcher records the
:class:`StallEvent` in its emit buffer. The dispatch loop drains the
buffer and forwards each event onto the lifecycle hook bus as
``agent.progress_stalled`` (see
:class:`bernstein.core.lifecycle.hooks.LifecycleEvent`).

References:

* Ticket: feat-2026-05-19-progress-watch-liveness-probe.
* Module map: ``src/bernstein/core/observability/`` (see CLAUDE.md).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: How long a registered log may be idle before the watcher flags it as
#: stalled. Operator-tunable via ``agents.progress_watch.inactivity_seconds``.
DEFAULT_INACTIVITY_SECONDS: int = 120

#: How long a stalled log may remain idle before the watcher requests an
#: escalation from SIGTERM to SIGKILL. Operator-tunable via
#: ``agents.progress_watch.kill_after_inactivity_seconds``.
DEFAULT_KILL_AFTER_INACTIVITY_SECONDS: int = 300

#: Default tick cadence for the dispatcher that owns the watcher.
DEFAULT_POLL_INTERVAL_SECONDS: int = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class _LogSnapshot:
    """Last observed stat() for a registered log file.

    ``mtime`` and ``size`` together are the activity signal: either one
    moving forward counts as progress. The watcher treats a missing file
    as "no progress yet" rather than an error, because adapters may
    register a log path before the CLI has created the file.
    """

    mtime: float = 0.0
    size: int = 0
    last_growth_ts: float = 0.0
    #: When the watcher first observed the log to be idle past the
    #: inactivity threshold. ``0.0`` means "not currently considered
    #: stalled". Used to compute the SIGTERM -> SIGKILL escalation gap.
    stalled_since_ts: float = 0.0


@dataclass(frozen=True)
class StallEvent:
    """A single ``agent.progress_stalled`` lifecycle event payload."""

    session_id: str
    adapter: str
    log_path: str
    last_log_growth_ts: float
    detected_ts: float


@dataclass(frozen=True)
class KillVerdict:
    """Result of :meth:`ProgressWatch.kill_if_stale`.

    Attributes:
        action: ``"none"`` (still healthy), ``"sigterm"`` (inactivity
            threshold crossed, request graceful kill), ``"sigkill"``
            (kill-after threshold crossed since SIGTERM, escalate).
        session_id: Echo of the queried session.
        idle_seconds: Seconds since the log last grew.
        reason: Human-readable detail for audit logs.
    """

    action: str
    session_id: str
    idle_seconds: float
    reason: str = ""


@dataclass
class _Registration:
    """A single registered session under watch."""

    session_id: str
    adapter: str
    log_path: Path
    snapshot: _LogSnapshot = field(default_factory=_LogSnapshot)


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


#: Stat function signature. Returns ``(mtime, size)`` for a path, or
#: raises ``OSError`` when the path is missing or unreadable. The
#: indirection exists so tests can drive the watcher without touching
#: the real filesystem.
StatFn = Callable[[Path], tuple[float, int]]


def _default_stat(path: Path) -> tuple[float, int]:
    """Default :data:`StatFn` -- ``(mtime, size)`` from ``os.stat``."""
    st = path.stat()
    return float(st.st_mtime), int(st.st_size)


class ProgressWatch:
    """Single-threaded liveness probe for session-log growth.

    The watcher is fully synchronous. Callers own the tick cadence and
    the kill mechanism. Internally we hold a lock to keep ``register``,
    ``unregister``, and ``tick`` safe against concurrent callers in case
    the dispatch loop uses a worker thread.
    """

    def __init__(
        self,
        *,
        inactivity_seconds: int = DEFAULT_INACTIVITY_SECONDS,
        kill_after_inactivity_seconds: int = DEFAULT_KILL_AFTER_INACTIVITY_SECONDS,
        clock: Callable[[], float] | None = None,
        stat_fn: StatFn | None = None,
    ) -> None:
        if inactivity_seconds <= 0:
            raise ValueError("inactivity_seconds must be positive")
        if kill_after_inactivity_seconds < inactivity_seconds:
            raise ValueError("kill_after_inactivity_seconds must be >= inactivity_seconds")
        self._inactivity_seconds = inactivity_seconds
        self._kill_after_inactivity_seconds = kill_after_inactivity_seconds
        self._clock: Callable[[], float] = clock or time.time
        self._stat_fn: StatFn = stat_fn or _default_stat
        self._lock = threading.Lock()
        self._registrations: dict[str, _Registration] = {}
        self._pending_events: list[StallEvent] = []

    # ------------------------------------------------------------------
    # Registration surface
    # ------------------------------------------------------------------

    def register(
        self,
        session_id: str,
        log_path: str | os.PathLike[str],
        *,
        adapter: str = "",
    ) -> None:
        """Begin watching ``log_path`` for the given session.

        Re-registering the same session_id replaces the previous entry.
        The watcher seeds the snapshot from the current stat() so the
        first :meth:`tick` does not produce a spurious "growth" event.
        """
        path = Path(log_path)
        snapshot = _LogSnapshot(last_growth_ts=self._clock())
        try:
            mtime, size = self._stat_fn(path)
        except OSError:
            # File does not exist yet; treat as zero-size. The watcher
            # will detect creation as growth on the next tick.
            pass
        else:
            snapshot.mtime = mtime
            snapshot.size = size
        with self._lock:
            self._registrations[session_id] = _Registration(
                session_id=session_id,
                adapter=adapter,
                log_path=path,
                snapshot=snapshot,
            )

    def unregister(self, session_id: str) -> None:
        """Stop watching ``session_id``. Idempotent."""
        with self._lock:
            self._registrations.pop(session_id, None)

    def is_registered(self, session_id: str) -> bool:
        """True iff the session is currently under watch."""
        with self._lock:
            return session_id in self._registrations

    def registered_sessions(self) -> list[str]:
        """Return the list of currently-watched session ids."""
        with self._lock:
            return list(self._registrations.keys())

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def tick(self) -> list[StallEvent]:
        """Sample every registered log; return newly-detected stalls.

        For every registered session we stat() the log file:

        * If mtime or size moved forward, the snapshot updates and the
          session is considered healthy.
        * Otherwise, if the idle gap exceeds ``inactivity_seconds`` and
          we have not yet emitted a stall for this idle period, the
          watcher records a :class:`StallEvent`. Subsequent ticks do
          not re-emit until the log grows again (the event is sticky).
        """
        now = self._clock()
        emitted: list[StallEvent] = []
        with self._lock:
            for reg in self._registrations.values():
                grew = self._sample(reg, now)
                if grew:
                    # Log advanced; clear sticky stall state.
                    reg.snapshot.stalled_since_ts = 0.0
                    continue
                idle = now - reg.snapshot.last_growth_ts
                if idle < self._inactivity_seconds:
                    continue
                if reg.snapshot.stalled_since_ts > 0.0:
                    # Already emitted for this idle stretch.
                    continue
                reg.snapshot.stalled_since_ts = now
                event = StallEvent(
                    session_id=reg.session_id,
                    adapter=reg.adapter,
                    log_path=str(reg.log_path),
                    last_log_growth_ts=reg.snapshot.last_growth_ts,
                    detected_ts=now,
                )
                emitted.append(event)
            self._pending_events.extend(emitted)
        return emitted

    def drain_pending_events(self) -> list[StallEvent]:
        """Return and clear the buffer of all stall events seen so far.

        The dispatch loop reads this buffer once per tick and forwards
        each event onto the lifecycle hook bus. Holding the buffer in
        the watcher lets a synchronous tick-then-emit caller stay free
        of any side-effects.
        """
        with self._lock:
            events = self._pending_events
            self._pending_events = []
        return events

    def kill_if_stale(self, session_id: str) -> KillVerdict:
        """Return the kill verdict for ``session_id`` without side effects.

        The dispatch loop calls this when it is deciding what signal to
        send to a worker process:

        * ``"none"`` -- log is still growing or session is unregistered.
        * ``"sigterm"`` -- inactivity threshold crossed, time to ask
          the worker to wind down gracefully.
        * ``"sigkill"`` -- the worker has been stalled for at least
          ``kill_after_inactivity_seconds``; escalate to a hard kill.
        """
        now = self._clock()
        with self._lock:
            reg = self._registrations.get(session_id)
            if reg is None:
                return KillVerdict(
                    action="none",
                    session_id=session_id,
                    idle_seconds=0.0,
                    reason="session not registered",
                )
            # Refresh snapshot so callers see the latest growth state.
            self._sample(reg, now)
            idle = now - reg.snapshot.last_growth_ts
            if idle < self._inactivity_seconds:
                return KillVerdict(
                    action="none",
                    session_id=session_id,
                    idle_seconds=idle,
                    reason="log still growing",
                )
            if idle >= self._kill_after_inactivity_seconds:
                return KillVerdict(
                    action="sigkill",
                    session_id=session_id,
                    idle_seconds=idle,
                    reason="kill-after threshold crossed",
                )
            return KillVerdict(
                action="sigterm",
                session_id=session_id,
                idle_seconds=idle,
                reason="inactivity threshold crossed",
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sample(self, reg: _Registration, now: float) -> bool:
        """Update ``reg.snapshot`` from a fresh stat(); return whether it grew."""
        try:
            mtime, size = self._stat_fn(reg.log_path)
        except OSError:
            # Missing file is "no growth"; do not raise out of the loop.
            return False
        grew = mtime > reg.snapshot.mtime or size > reg.snapshot.size
        if grew:
            reg.snapshot.mtime = mtime
            reg.snapshot.size = size
            reg.snapshot.last_growth_ts = now
        return grew
