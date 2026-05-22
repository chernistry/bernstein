"""Deterministic replay recorder for orchestration runs.

Records every significant event during an orchestration run to a JSONL file
at `.sdd/runs/{run_id}/replay.jsonl`. The replay log enables:
  - Post-hoc debugging: see exactly what each agent saw and produced.
  - Reproducibility proof: SHA-256 fingerprint of the full event stream.
  - `bernstein replay <run_id>`: step-by-step playback in the terminal.

Usage:
    recorder = RunRecorder(run_id="20240315-143022", sdd_dir=Path(".sdd"))
    recorder.record("task_claimed", task_id="T-001", agent_id="backend-abc", model="sonnet")
    recorder.record("agent_spawned", agent_id="backend-abc", prompt_hash="sha256:abc123")
    recorder.record("task_completed", task_id="T-001", files_modified=["src/auth.py"], cost_usd=0.12)
    fingerprint = recorder.fingerprint()
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from bernstein.core.defaults import JANITOR
from bernstein.core.persistence.runtime_state import rotate_log_file

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)

#: Event fields that vary across runs even when the execution is identical.
#: They stay in ``replay.jsonl`` (operators want the timeline) but are excluded
#: from the determinism fingerprint, which must be byte-stable across runs.
#: Keep this set limited to provably non-deterministic envelope fields:
#: over-excluding a real decision field would let two genuinely different runs
#: collide on the same fingerprint (issue #1851).
_NON_DETERMINISTIC_FIELDS = frozenset({"ts", "elapsed_s"})


def _canonical_event_bytes(event: dict[str, Any]) -> bytes:
    """Return canonical bytes for one event, excluding the timing envelope.

    The wall-clock fields in :data:`_NON_DETERMINISTIC_FIELDS` are dropped and
    the remaining keys are JSON-encoded with sorted keys and fixed separators,
    so two recordings of the same decision stream hash identically regardless
    of timing or incidental key order. Mirrors the canonical-bytes discipline
    used by the audit log and lineage entries.

    Args:
        event: One decoded ``replay.jsonl`` row.

    Returns:
        UTF-8 canonical JSON bytes of the deterministic projection.
    """
    projected = {k: v for k, v in event.items() if k not in _NON_DETERMINISTIC_FIELDS}
    return json.dumps(projected, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _fingerprint_lines(lines: Iterable[str]) -> str:
    """Hash the deterministic projection of each non-blank JSONL line.

    Lines that fail to parse as JSON are skipped (mirroring
    :func:`load_replay_events`) so a partial trailing write cannot wedge the
    fingerprint. The hash covers ``event`` plus the decision-relevant payload
    and excludes the wall-clock envelope (issue #1851).
    """
    sha = hashlib.sha256()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            # Defensive: a bare scalar/array line is not a recordable event.
            continue
        sha.update(_canonical_event_bytes(event))
        sha.update(b"\n")
    return sha.hexdigest()


class RunRecorder:
    """Append-only JSONL recorder for a single orchestration run.

    Thread-safe for single-writer usage (the orchestrator tick loop is
    single-threaded). File is opened/closed per write to avoid holding
    file handles across long tick intervals.

    Args:
        run_id: Unique identifier for the run (e.g. ``"20240315-143022"``).
        sdd_dir: Path to the ``.sdd`` directory.
    """

    def __init__(self, run_id: str, sdd_dir: Path) -> None:
        self._run_id = run_id
        self._path = sdd_dir / "runs" / run_id / "replay.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._start_ts: float = time.time()

    @property
    def run_id(self) -> str:
        """The run identifier this recorder is writing to."""
        return self._run_id

    @property
    def path(self) -> Path:
        """Path to the replay JSONL file."""
        return self._path

    def record(self, event: str, **data: Any) -> None:
        """Append a single event to the replay log.

        Args:
            event: Event type (e.g. ``"task_claimed"``, ``"agent_spawned"``).
            **data: Arbitrary key-value pairs for the event payload.
        """
        entry: dict[str, Any] = {
            "ts": time.time(),
            "elapsed_s": round(time.time() - self._start_ts, 3),
            "event": event,
        }
        entry.update(data)
        # cap unbounded replay.jsonl. `bernstein replay` may stitch
        # live + rotated backups if needed - see load_replay_events.
        rotate_log_file(self._path, max_bytes=JANITOR.replay_rotate_bytes)
        try:
            with self._path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:
            logger.warning("RunRecorder: failed to write event %r: %s", event, exc)

    def fingerprint(self) -> str:
        """Compute the deterministic execution fingerprint of the replay log.

        Hashes a canonical projection of each event that keeps ``event`` and
        the decision-relevant payload but excludes the wall-clock envelope
        (``ts`` / ``elapsed_s``), so two byte-identical executions hash equal
        regardless of timing (issue #1851). The on-disk log is unchanged; only
        the fingerprint computation skips the timing fields.

        Returns:
            Hex-encoded SHA-256 hash, or empty string if the file doesn't exist.
        """
        if not self._path.exists():
            return ""
        try:
            with self._path.open(encoding="utf-8") as f:
                return _fingerprint_lines(f)
        except OSError as exc:
            logger.warning("RunRecorder: failed to read replay log for fingerprint: %s", exc)
            return ""

    def event_count(self) -> int:
        """Return the number of events recorded so far."""
        if not self._path.exists():
            return 0
        try:
            with self._path.open() as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0


def load_replay_events(replay_path: Path) -> list[dict[str, Any]]:
    """Load all events from a replay JSONL file.

    Args:
        replay_path: Path to the ``replay.jsonl`` file.

    Returns:
        List of event dicts, ordered by timestamp.
    """
    events: list[dict[str, Any]] = []
    if not replay_path.exists():
        return events
    with replay_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def compute_replay_fingerprint(replay_path: Path) -> str:
    """Compute the deterministic execution fingerprint of a replay log file.

    Hashes the same canonical, timing-excluded projection as
    :meth:`RunRecorder.fingerprint`, so a recording and a faithful replay -
    which differ only in their ``ts`` / ``elapsed_s`` envelope - share one
    fingerprint, while any divergence in the decision stream changes it
    (issue #1851).

    Args:
        replay_path: Path to the ``replay.jsonl`` file.

    Returns:
        Hex-encoded SHA-256 hash, or empty string if the file doesn't exist.
    """
    if not replay_path.exists():
        return ""
    try:
        with replay_path.open(encoding="utf-8") as f:
            return _fingerprint_lines(f)
    except OSError as exc:
        logger.warning("compute_replay_fingerprint: failed to read %s: %s", replay_path, exc)
        return ""
