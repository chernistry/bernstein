"""Unit tests for the ProgressWatch liveness probe.

Coverage:

* register / unregister / is_registered round-trip
* tick() reports a stall once the inactivity window is crossed
* tick() does not re-emit on subsequent stalled ticks (sticky event)
* a log that grows after a stall clears the sticky state
* kill_if_stale escalates from sigterm to sigkill at the kill-after window
* missing log files are treated as no-growth, not as errors
* constructor validates the threshold relationship
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.observability.progress_watch import (
    DEFAULT_INACTIVITY_SECONDS,
    DEFAULT_KILL_AFTER_INACTIVITY_SECONDS,
    KillVerdict,
    ProgressWatch,
    StallEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Manually-advanced monotonic clock for deterministic tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class _FakeFs:
    """Inject a stat() over an in-memory ``{path: (mtime, size)}`` map."""

    def __init__(self) -> None:
        self.files: dict[Path, tuple[float, int]] = {}

    def __call__(self, path: Path) -> tuple[float, int]:
        try:
            return self.files[path]
        except KeyError as exc:
            raise FileNotFoundError(str(path)) from exc

    def write(self, path: Path, mtime: float, size: int) -> None:
        self.files[path] = (mtime, size)


def _make_watch(
    *,
    inactivity: int = 60,
    kill_after: int = 120,
) -> tuple[ProgressWatch, _FakeClock, _FakeFs]:
    clock = _FakeClock()
    fs = _FakeFs()
    watch = ProgressWatch(
        inactivity_seconds=inactivity,
        kill_after_inactivity_seconds=kill_after,
        clock=clock,
        stat_fn=fs,
    )
    return watch, clock, fs


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_thresholds_match_documented_constants() -> None:
    watch = ProgressWatch()
    assert DEFAULT_INACTIVITY_SECONDS > 0
    assert DEFAULT_KILL_AFTER_INACTIVITY_SECONDS >= DEFAULT_INACTIVITY_SECONDS
    # Sanity-check the runtime state is observable.
    assert watch.registered_sessions() == []


def test_constructor_rejects_non_positive_inactivity() -> None:
    with pytest.raises(ValueError, match="inactivity_seconds"):
        ProgressWatch(inactivity_seconds=0)


def test_constructor_rejects_kill_after_smaller_than_inactivity() -> None:
    with pytest.raises(ValueError, match="kill_after_inactivity_seconds"):
        ProgressWatch(inactivity_seconds=120, kill_after_inactivity_seconds=60)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_seeds_snapshot_from_current_stat() -> None:
    watch, clock, fs = _make_watch()
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=42)
    watch.register("s1", log, adapter="claude")

    assert watch.is_registered("s1")
    assert watch.registered_sessions() == ["s1"]
    # First tick at t+1s with no growth should not produce an event.
    clock.advance(1.0)
    assert watch.tick() == []


def test_register_tolerates_missing_log_file() -> None:
    watch, clock, fs = _make_watch()
    log = Path("/tmp/not-yet-created.log")
    # File deliberately absent from fs.
    watch.register("s1", log, adapter="codex")
    # A first tick observes no growth; no immediate stall.
    clock.advance(1.0)
    assert watch.tick() == []
    # When the file appears, growth is detected.
    fs.write(log, mtime=clock.now, size=10)
    clock.advance(1.0)
    assert watch.tick() == []  # growth, no stall


def test_unregister_is_idempotent() -> None:
    watch, _clock, fs = _make_watch()
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=1.0, size=1)
    watch.register("s1", log)
    watch.unregister("s1")
    watch.unregister("s1")  # second call must not raise
    assert not watch.is_registered("s1")


# ---------------------------------------------------------------------------
# Tick: stall detection
# ---------------------------------------------------------------------------


def test_tick_emits_stall_after_inactivity_window() -> None:
    watch, clock, fs = _make_watch(inactivity=60, kill_after=120)
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=10)
    watch.register("s1", log, adapter="claude")

    # Mid-window tick: no stall.
    clock.advance(30.0)
    assert watch.tick() == []

    # Past the inactivity threshold: one stall event.
    clock.advance(31.0)
    events = watch.tick()
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StallEvent)
    assert event.session_id == "s1"
    assert event.adapter == "claude"
    assert event.log_path == str(log)
    assert event.last_log_growth_ts == 1_000.0
    assert event.detected_ts == clock.now


def test_tick_does_not_re_emit_for_continued_stall() -> None:
    watch, clock, fs = _make_watch(inactivity=60, kill_after=300)
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=10)
    watch.register("s1", log, adapter="claude")

    clock.advance(61.0)
    first = watch.tick()
    assert len(first) == 1

    # Several more ticks past the threshold should stay quiet.
    for _ in range(3):
        clock.advance(10.0)
        assert watch.tick() == []


def test_log_growth_clears_sticky_stall_state() -> None:
    watch, clock, fs = _make_watch(inactivity=60, kill_after=300)
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=10)
    watch.register("s1", log, adapter="claude")

    clock.advance(61.0)
    assert len(watch.tick()) == 1

    # Agent makes progress: log size grows.
    clock.advance(5.0)
    fs.write(log, mtime=clock.now, size=20)
    assert watch.tick() == []

    # Now go idle again; a fresh stall event must fire.
    clock.advance(61.0)
    events = watch.tick()
    assert len(events) == 1
    assert events[0].session_id == "s1"


def test_drain_pending_events_returns_and_clears() -> None:
    watch, clock, fs = _make_watch(inactivity=60, kill_after=300)
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=10)
    watch.register("s1", log, adapter="claude")
    clock.advance(61.0)
    watch.tick()

    drained = watch.drain_pending_events()
    assert len(drained) == 1
    # Second drain is empty.
    assert watch.drain_pending_events() == []


# ---------------------------------------------------------------------------
# kill_if_stale verdicts
# ---------------------------------------------------------------------------


def test_kill_if_stale_returns_none_for_unknown_session() -> None:
    watch, _clock, _fs = _make_watch()
    verdict = watch.kill_if_stale("missing")
    assert isinstance(verdict, KillVerdict)
    assert verdict.action == "none"
    assert "not registered" in verdict.reason


def test_kill_if_stale_progresses_through_sigterm_then_sigkill() -> None:
    watch, clock, fs = _make_watch(inactivity=60, kill_after=120)
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=10)
    watch.register("s1", log, adapter="claude")

    # Inside the inactivity window: healthy.
    clock.advance(30.0)
    verdict = watch.kill_if_stale("s1")
    assert verdict.action == "none"

    # Past inactivity but before kill-after: graceful kill.
    clock.advance(40.0)  # now 70s idle
    verdict = watch.kill_if_stale("s1")
    assert verdict.action == "sigterm"
    assert verdict.idle_seconds >= 60.0

    # Past the kill-after threshold: hard kill.
    clock.advance(60.0)  # now 130s idle
    verdict = watch.kill_if_stale("s1")
    assert verdict.action == "sigkill"
    assert verdict.idle_seconds >= 120.0


def test_kill_if_stale_resets_when_log_grows() -> None:
    watch, clock, fs = _make_watch(inactivity=60, kill_after=120)
    log = Path("/tmp/agent.log")
    fs.write(log, mtime=clock.now, size=10)
    watch.register("s1", log, adapter="claude")

    clock.advance(70.0)
    assert watch.kill_if_stale("s1").action == "sigterm"

    # Agent makes progress; the next verdict goes back to ``none``.
    clock.advance(1.0)
    fs.write(log, mtime=clock.now, size=20)
    assert watch.kill_if_stale("s1").action == "none"
