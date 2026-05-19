"""Classify Bernstein worktrees as active / orphan / stale / corrupt.

The classifier is the single source of truth shared by

* the ``bernstein worktrees list`` CLI subcommand,
* ``bernstein worktrees gc`` reaper, and
* the TUI list pane refreshed every 10s.

Inputs (all read-only):

* ``git worktree list --porcelain`` in the project repo — definitive
  list of every git-registered worktree.
* ``.sdd/runtime/pids/<session_id>.json`` — task / worker PID record.
* ``.sdd/traces/<session_id>.jsonl`` — last-trace mtime for staleness.
* The on-disk worktree directory itself for size and ``.git`` presence.

The classifier never modifies state. ``reap_worktree`` performs the only
destructive action and is gated behind ``.sdd/runtime/worktree-gc.lock``.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "GC_LOCK_RELPATH",
    "STALE_TRACE_AGE_S",
    "WORKTREE_GC_LIFECYCLE_EVENT",
    "ClassifiedWorktree",
    "WorktreeState",
    "classify_worktrees",
    "format_size",
    "iter_worktree_dirs",
    "reap_worktree",
    "worktrees_root",
]


#: Repo-relative path of the GC lock file. Single-file lock prevents two
#: concurrent operators (or an operator plus a daemon) from reaping the
#: same directory and corrupting git state.
GC_LOCK_RELPATH = ".sdd/runtime/worktree-gc.lock"

#: How old the last trace event must be before a dead-PID worktree is
#: considered ``stale``. Anything younger stays ``active`` so we never
#: race against an agent that briefly lost its PID file.
STALE_TRACE_AGE_S: int = 24 * 60 * 60

#: Lifecycle event identifier emitted for each reaped worktree.
#: Plugins subscribe to this string via the ``bernstein.core.lifecycle``
#: registry. Adding a brand-new enum entry would ripple through the
#: notify bridge, so the classifier uses a free-form event id instead.
WORKTREE_GC_LIFECYCLE_EVENT = "worktree.gc"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class WorktreeState(StrEnum):
    """Deterministic state assigned to every worktree.

    The four states are mutually exclusive; the classifier picks the
    first matching rule in this order: ``corrupt`` > ``orphan`` >
    ``stale`` > ``active``.
    """

    ACTIVE = "active"
    ORPHAN = "orphan"
    STALE = "stale"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ClassifiedWorktree:
    """One row in the ``bernstein worktrees list`` table.

    Attributes:
        path: Absolute filesystem path of the worktree directory.
        session_id: Directory basename — Bernstein uses the session id
            as the worktree slug, so this also identifies the owning
            task when one exists.
        task_id: Task identifier from the PID record, or ``None`` when
            no task record was found.
        state: Classified :class:`WorktreeState`.
        age_seconds: Wall-clock age of the worktree directory, computed
            from its ``ctime`` (creation when the FS reports it,
            metadata-change otherwise).
        size_bytes: Recursive size on disk in bytes (best effort —
            unreadable entries are skipped silently).
        pid: Worker PID read from the task record, or ``None``.
        pid_alive: Whether ``os.kill(pid, 0)`` succeeded. ``False`` when
            ``pid`` is ``None``.
        last_trace_mtime: Unix timestamp of the most recent trace
            event, or ``None`` if no trace file exists.
    """

    path: Path
    session_id: str
    task_id: str | None
    state: WorktreeState
    age_seconds: float
    size_bytes: int
    pid: int | None
    pid_alive: bool
    last_trace_mtime: float | None

    @property
    def is_reapable(self) -> bool:
        """Return ``True`` when the worktree is safe to delete."""
        return self.state in (WorktreeState.ORPHAN, WorktreeState.STALE, WorktreeState.CORRUPT)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def worktrees_root(repo_root: Path) -> Path:
    """Return the directory under which Bernstein stores agent worktrees.

    The spec describes ``.sdd/runtime/worktrees/`` while the current
    codebase still writes to ``.sdd/worktrees/``. We honour the new
    location when it exists, otherwise fall back to the legacy path.
    """
    runtime = repo_root / ".sdd" / "runtime" / "worktrees"
    if runtime.is_dir():
        return runtime
    return repo_root / ".sdd" / "worktrees"


def iter_worktree_dirs(repo_root: Path) -> list[Path]:
    """Return every directory that looks like an agent worktree.

    Skips the ``.locks`` bookkeeping directory used by
    :mod:`bernstein.core.git.worktree`.
    """
    root = worktrees_root(repo_root)
    if not root.is_dir():
        return []
    entries: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name == "locks":
            continue
        entries.append(entry)
    return entries


def format_size(size_bytes: int) -> str:
    """Render a byte count as a short human-readable string."""
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(size_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size_bytes} B"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_worktrees(
    repo_root: Path,
    *,
    now: float | None = None,
    stale_trace_age_s: int = STALE_TRACE_AGE_S,
) -> list[ClassifiedWorktree]:
    """Classify every Bernstein worktree under ``repo_root``.

    Args:
        repo_root: Absolute path to the repository root.
        now: Override the wall-clock for tests; defaults to ``time.time()``.
        stale_trace_age_s: Trace freshness threshold in seconds.

    Returns:
        One :class:`ClassifiedWorktree` per directory, sorted by name.
    """
    clock = time.time() if now is None else now
    git_paths = _git_worktree_paths(repo_root)
    rows: list[ClassifiedWorktree] = [
        _classify_one(
            path,
            repo_root=repo_root,
            git_paths=git_paths,
            now=clock,
            stale_trace_age_s=stale_trace_age_s,
        )
        for path in iter_worktree_dirs(repo_root)
    ]
    return rows


def _classify_one(
    path: Path,
    *,
    repo_root: Path,
    git_paths: frozenset[str],
    now: float,
    stale_trace_age_s: int,
) -> ClassifiedWorktree:
    session_id = path.name
    size_bytes = _dir_size(path)
    age_seconds = _dir_age(path, now=now)

    # 1. Corrupt — directory exists but git can't see a .git anchor.
    git_anchor = path / ".git"
    if not git_anchor.exists():
        return ClassifiedWorktree(
            path=path,
            session_id=session_id,
            task_id=None,
            state=WorktreeState.CORRUPT,
            age_seconds=age_seconds,
            size_bytes=size_bytes,
            pid=None,
            pid_alive=False,
            last_trace_mtime=None,
        )

    # Load the task record, if any.
    pid_record = _read_pid_record(repo_root, session_id)
    task_id = pid_record.get("task_id") if pid_record else None
    pid = _coerce_pid(pid_record)
    alive = pid is not None and _process_alive(pid)
    last_trace_mtime = _last_trace_mtime(repo_root, session_id)

    # 2. Orphan — directory has no task record at all.
    if pid_record is None:
        return ClassifiedWorktree(
            path=path,
            session_id=session_id,
            task_id=None,
            state=WorktreeState.ORPHAN,
            age_seconds=age_seconds,
            size_bytes=size_bytes,
            pid=pid,
            pid_alive=alive,
            last_trace_mtime=last_trace_mtime,
        )

    # 3. Active — task record exists and PID is alive.
    if alive:
        return ClassifiedWorktree(
            path=path,
            session_id=session_id,
            task_id=task_id if isinstance(task_id, str) else None,
            state=WorktreeState.ACTIVE,
            age_seconds=age_seconds,
            size_bytes=size_bytes,
            pid=pid,
            pid_alive=True,
            last_trace_mtime=last_trace_mtime,
        )

    # 4. Stale — task record exists but PID dead AND last trace > threshold.
    # If trace freshness is below the threshold we cannot prove staleness
    # yet, so leave the worktree marked ``active`` to be safe. The
    # operator can re-run ``gc`` later.
    trace_age = (now - last_trace_mtime) if last_trace_mtime is not None else float("inf")
    if trace_age > stale_trace_age_s:
        return ClassifiedWorktree(
            path=path,
            session_id=session_id,
            task_id=task_id if isinstance(task_id, str) else None,
            state=WorktreeState.STALE,
            age_seconds=age_seconds,
            size_bytes=size_bytes,
            pid=pid,
            pid_alive=False,
            last_trace_mtime=last_trace_mtime,
        )

    return ClassifiedWorktree(
        path=path,
        session_id=session_id,
        task_id=task_id if isinstance(task_id, str) else None,
        state=WorktreeState.ACTIVE,
        age_seconds=age_seconds,
        size_bytes=size_bytes,
        pid=pid,
        pid_alive=False,
        last_trace_mtime=last_trace_mtime,
    )


# ---------------------------------------------------------------------------
# Reaper
# ---------------------------------------------------------------------------


def reap_worktree(
    repo_root: Path,
    worktree: ClassifiedWorktree,
    *,
    dry_run: bool = False,
) -> bool:
    """Delete the worktree directory and prune git state.

    The caller MUST hold the GC lock at :data:`GC_LOCK_RELPATH`. This
    function never acquires the lock on its own — leave that decision to
    the CLI / TUI driver so a batch reap takes the lock once.

    Args:
        repo_root: Absolute repository root.
        worktree: Classifier output for the directory to delete.
        dry_run: When ``True``, no filesystem mutation happens; the
            function returns ``True`` to mirror a real successful reap.

    Returns:
        ``True`` when the directory was removed (or would have been in
        dry-run mode); ``False`` if the directory was already gone.
    """
    target = worktree.path
    if not target.exists():
        logger.info("reap: %s already gone, skipping", target)
        return False

    if dry_run:
        logger.info("reap (dry-run): would remove %s", target)
        return True

    try:
        shutil.rmtree(target)
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("reap: failed to remove %s: %s", target, exc)
        return False

    # Best-effort: tell git the worktree is gone.
    try:
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("reap: git worktree prune failed: %s", exc)

    return True


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _git_worktree_paths(repo_root: Path) -> frozenset[str]:
    """Return absolute paths git considers active worktrees."""
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("git worktree list failed: %s", exc)
        return frozenset()
    if proc.returncode != 0:
        return frozenset()
    paths: set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(line[len("worktree ") :].strip())
    return frozenset(paths)


def _read_pid_record(repo_root: Path, session_id: str) -> dict[str, object] | None:
    pid_file = repo_root / ".sdd" / "runtime" / "pids" / f"{session_id}.json"
    if not pid_file.exists():
        return None
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _coerce_pid(record: dict[str, object] | None) -> int | None:
    if record is None:
        return None
    candidate = record.get("worker_pid") or record.get("pid")
    if candidate is None:
        return None
    try:
        value = int(candidate)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _process_alive(pid: int) -> bool:
    """Return ``True`` when ``pid`` is a live process.

    Uses ``os.kill(pid, 0)`` and treats ``EPERM`` as alive (the process
    exists but we lack permission to signal it).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return exc.errno == errno.EPERM
    return True


def _last_trace_mtime(repo_root: Path, session_id: str) -> float | None:
    trace_file = repo_root / ".sdd" / "traces" / f"{session_id}.jsonl"
    try:
        return trace_file.stat().st_mtime
    except OSError:
        return None


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _exc: None):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _dir_age(path: Path, *, now: float) -> float:
    try:
        stat = path.stat()
    except OSError:
        return 0.0
    # ``st_birthtime`` is more accurate where available (macOS, BSD);
    # fall back to ``st_ctime`` on Linux.
    birth = getattr(stat, "st_birthtime", None)
    created = birth if isinstance(birth, (int, float)) else stat.st_ctime
    return max(0.0, now - float(created))
