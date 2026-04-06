"""CFG-006: Hot-reload for runtime config changes.

Watch bernstein.yaml for modifications and signal the orchestrator when
the config changes.  Uses the existing ConfigWatcher for drift detection
and triggers a reload callback when drift is confirmed.

The reloader is purely deterministic -- it polls file checksums on a
configurable interval rather than relying on OS-level file watchers
(inotify/kqueue) which can miss edits in some container/VM setups.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from bernstein.core.config_diff import (
    ConfigDiffSummary,
    diff_config_snapshots,
    load_redacted_config,
)
from bernstein.core.config_watcher import ConfigWatcher, DriftReport

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Default poll interval for hot-reload checking (seconds).
DEFAULT_POLL_INTERVAL_S: float = 5.0

# Minimum interval between successive reloads to prevent thrashing.
MIN_RELOAD_INTERVAL_S: float = 2.0


class ReloadCallback(Protocol):
    """Protocol for config reload notification callbacks."""

    def __call__(self, diff: ConfigDiffSummary) -> None:
        """Called when config changes are detected and reloaded.

        Args:
            diff: Summary of what changed between old and new config.
        """
        ...


@dataclass(frozen=True, slots=True)
class ReloadEvent:
    """Record of a single config reload.

    Attributes:
        timestamp: Unix timestamp when the reload occurred.
        diff: Summary of changes detected.
        source_path: Path to the config file that changed.
        success: Whether the reload was applied successfully.
        error: Error message if reload failed.
    """

    timestamp: float
    diff: ConfigDiffSummary
    source_path: str
    success: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "timestamp": self.timestamp,
            "diff": self.diff.to_dict(),
            "source_path": self.source_path,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class HotReloader:
    """Watches config files and triggers reload on change.

    Uses :class:`ConfigWatcher` for drift detection and computes a
    redacted diff for each reload event.  Callbacks are invoked
    synchronously in the polling thread/task.

    Attributes:
        workdir: Project root directory.
        poll_interval_s: Seconds between drift checks.
        watcher: Underlying file-checksum watcher.
        callbacks: Registered reload notification callbacks.
        history: Log of past reload events (bounded).
        max_history: Maximum number of reload events to retain.
    """

    workdir: Path
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S
    watcher: ConfigWatcher | None = field(default=None, repr=False)
    callbacks: list[ReloadCallback] = field(default_factory=list)
    history: list[ReloadEvent] = field(default_factory=list)
    max_history: int = 50
    _last_reload_ts: float = field(default=0.0, repr=False)
    _previous_snapshot: Any = field(default=None, repr=False)
    _running: bool = field(default=False, repr=False)

    def start(self) -> None:
        """Initialize the watcher and take a baseline snapshot.

        Must be called before :meth:`check` or :meth:`run_async`.
        """
        self.watcher = ConfigWatcher.snapshot(self.workdir)
        config_path = self.workdir / "bernstein.yaml"
        self._previous_snapshot = load_redacted_config(config_path)
        self._running = True
        logger.info("Hot-reloader started for %s (poll=%.1fs)", self.workdir, self.poll_interval_s)

    def stop(self) -> None:
        """Signal the reloader to stop."""
        self._running = False
        logger.info("Hot-reloader stopped")

    def register_callback(self, callback: ReloadCallback) -> None:
        """Register a callback to invoke on config reload.

        Args:
            callback: Function called with a ConfigDiffSummary on reload.
        """
        self.callbacks.append(callback)

    def check(self) -> ReloadEvent | None:
        """Poll for config changes and trigger reload if needed.

        Returns:
            A :class:`ReloadEvent` if a reload was triggered, else None.
        """
        if self.watcher is None:
            return None

        now = time.time()
        if now - self._last_reload_ts < MIN_RELOAD_INTERVAL_S:
            return None

        report: DriftReport = self.watcher.check()
        if not report.drifted:
            return None

        return self._handle_drift(report, now)

    def _handle_drift(self, report: DriftReport, now: float) -> ReloadEvent:
        """Process a drift report, compute diff, and notify callbacks.

        Args:
            report: Drift report from the watcher.
            now: Current timestamp.

        Returns:
            The reload event that was created.
        """
        config_path = self.workdir / "bernstein.yaml"
        current_snapshot = load_redacted_config(config_path)

        diff = diff_config_snapshots(
            self._previous_snapshot if self._previous_snapshot is not None else {},
            current_snapshot,
        )

        source_paths = [e.path for e in report.events]
        source_path = source_paths[0] if source_paths else str(config_path)

        error = ""
        success = True
        try:
            for callback in self.callbacks:
                callback(diff)
        except Exception as exc:
            error = str(exc)
            success = False
            logger.error("Hot-reload callback failed: %s", exc)

        event = ReloadEvent(
            timestamp=now,
            diff=diff,
            source_path=source_path,
            success=success,
            error=error,
        )

        self._previous_snapshot = current_snapshot
        self._last_reload_ts = now
        self.history.append(event)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history :]

        # Acknowledge drift so it is not re-reported until next change.
        assert self.watcher is not None
        self.watcher.acknowledge_report(report)

        logger.info(
            "Config hot-reloaded: %d added, %d removed, %d modified",
            diff.added,
            diff.removed,
            diff.modified,
        )

        return event

    async def run_async(self) -> None:
        """Run the hot-reload polling loop as an async task.

        This coroutine runs until :meth:`stop` is called, checking for
        config drift on each interval.
        """
        if self.watcher is None:
            self.start()

        while self._running:
            self.check()
            await asyncio.sleep(self.poll_interval_s)

    @property
    def is_running(self) -> bool:
        """Whether the reloader is currently active."""
        return self._running

    @property
    def reload_count(self) -> int:
        """Total number of reloads that have occurred."""
        return len(self.history)
