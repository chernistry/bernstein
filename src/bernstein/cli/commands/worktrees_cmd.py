"""``bernstein worktrees`` — inspect and reap orphan worktrees.

Two subcommands::

    bernstein worktrees list           # tabular dump of every worktree
    bernstein worktrees gc [--yes] [--dry]

The classifier in :mod:`bernstein.core.worktrees.classifier` is the
source of truth for state. This module only handles I/O: rendering the
table, holding the GC lock, prompting the operator, and emitting the
``worktree.gc`` lifecycle event for plugins.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from bernstein.core.worktrees.classifier import (
    GC_LOCK_RELPATH,
    WORKTREE_GC_LIFECYCLE_EVENT,
    ClassifiedWorktree,
    WorktreeState,
    classify_worktrees,
    format_size,
    reap_worktree,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

__all__ = ["format_age", "lock_gc", "render_worktrees_table", "worktrees_group"]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


_STATE_STYLE: dict[WorktreeState, str] = {
    WorktreeState.ACTIVE: "green",
    WorktreeState.ORPHAN: "yellow",
    WorktreeState.STALE: "red",
    WorktreeState.CORRUPT: "magenta",
}


def format_age(seconds: float) -> str:
    """Render a wall-clock duration as ``"3d 04h"`` / ``"5m"`` / ``"42s"``."""
    secs = max(0, int(seconds))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86_400:
        hours = secs // 3600
        mins = (secs % 3600) // 60
        return f"{hours}h {mins:02d}m"
    days = secs // 86_400
    hours = (secs % 86_400) // 3600
    return f"{days}d {hours:02d}h"


def render_worktrees_table(rows: Iterable[ClassifiedWorktree]) -> Table:
    """Build a Rich table for ``bernstein worktrees list``."""
    table = Table(title="Bernstein worktrees", header_style="bold cyan")
    table.add_column("Path", overflow="fold", no_wrap=False)
    table.add_column("Task")
    table.add_column("State")
    table.add_column("Age", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("PID", justify="right")

    for row in rows:
        style = _STATE_STYLE.get(row.state, "white")
        task_display = row.task_id[:12] if row.task_id else "—"
        pid_display = "—" if row.pid is None else (f"{row.pid}" + ("" if row.pid_alive else "✗"))
        table.add_row(
            str(row.path),
            task_display,
            f"[{style}]{row.state.value}[/{style}]",
            format_age(row.age_seconds),
            format_size(row.size_bytes),
            pid_display,
        )
    return table


def _rows_to_json(rows: Iterable[ClassifiedWorktree]) -> list[dict[str, object]]:
    return [
        {
            "path": str(r.path),
            "session_id": r.session_id,
            "task_id": r.task_id,
            "state": r.state.value,
            "age_seconds": int(r.age_seconds),
            "size_bytes": r.size_bytes,
            "pid": r.pid,
            "pid_alive": r.pid_alive,
            "last_trace_mtime": r.last_trace_mtime,
            "reapable": r.is_reapable,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


class GcLockError(RuntimeError):
    """Raised when the GC lock cannot be acquired."""


@contextlib.contextmanager
def lock_gc(repo_root: Path):  # type: ignore[no-untyped-def]
    """Acquire :data:`GC_LOCK_RELPATH` exclusively, yielding the lock path.

    The lock file is created with ``O_EXCL``; a concurrent invocation
    sees the file and raises :class:`GcLockError`. The lock is removed
    on context exit even when the body raises.
    """
    lock_path = repo_root / GC_LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GcLockError(f"another worktree GC is already running ({lock_path})") from exc

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            payload = {"pid": os.getpid(), "started_at": time.time()}
            fh.write(json.dumps(payload))
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except OSError:
            logger.debug("lock release: %s already removed", lock_path)


# ---------------------------------------------------------------------------
# Lifecycle event helper
# ---------------------------------------------------------------------------


def _emit_worktree_gc(repo_root: Path, row: ClassifiedWorktree, dry_run: bool) -> None:
    """Notify plugins that ``row`` was reaped.

    We import lazily so importing this CLI module never drags in pluggy
    when the operator only ran ``--help``.
    """
    try:
        from bernstein.core.lifecycle.hooks import HookRegistry, LifecycleContext, LifecycleEvent
    except Exception:
        return

    registry = _shared_registry()
    if registry is None:
        return

    # ``LifecycleEvent`` is a closed StrEnum and we deliberately do not
    # add a new entry to avoid rippling through the notify bridge. We
    # piggyback on ``POST_ARCHIVE`` semantically (lifecycle-end) and pass
    # the canonical event id via the ``env`` payload.
    ctx = LifecycleContext(
        event=LifecycleEvent.POST_ARCHIVE,
        task=row.task_id,
        session_id=row.session_id,
        workdir=repo_root,
        env={
            "BERNSTEIN_WORKTREE_GC_EVENT": WORKTREE_GC_LIFECYCLE_EVENT,
            "BERNSTEIN_WORKTREE_GC_STATE": row.state.value,
            "BERNSTEIN_WORKTREE_GC_PATH": str(row.path),
            "BERNSTEIN_WORKTREE_GC_DRY_RUN": "1" if dry_run else "0",
        },
    )
    try:
        # ``HookRegistry`` may not implement an event-name-string overload;
        # the shared registry uses canonical enum events. We call the
        # standard fire path so any plugin that subscribes to
        # ``post_archive`` sees the env payload above and can filter by
        # ``BERNSTEIN_WORKTREE_GC_EVENT``.
        registry.run(LifecycleEvent.POST_ARCHIVE, ctx)
    except Exception as exc:
        logger.debug("lifecycle emit failed: %s", exc)
    _ = HookRegistry  # silence unused import warning when registry is None


def _shared_registry():  # type: ignore[no-untyped-def]
    """Return the process-wide :class:`HookRegistry`, if one is installed.

    Bernstein bootstrap stashes a singleton on a module-level attribute.
    The lookup is intentionally defensive — running the CLI as a
    standalone script should not require the orchestrator to be alive.
    """
    try:
        from bernstein.core.lifecycle import hooks as _hooks_mod
    except Exception:
        return None
    return getattr(_hooks_mod, "GLOBAL_REGISTRY", None)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


@click.group("worktrees")
def worktrees_group() -> None:
    """Inspect and reap Bernstein agent worktrees."""


@worktrees_group.command("list")
@click.option(
    "--workdir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("."),
    show_default=True,
    help="Project root containing .sdd/.",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
def list_cmd(workdir: Path, as_json: bool) -> None:
    """List every Bernstein worktree and its state."""
    repo_root = workdir.resolve()
    rows = classify_worktrees(repo_root)
    if as_json:
        click.echo(json.dumps(_rows_to_json(rows), indent=2, default=str))
        return

    console = Console()
    if not rows:
        console.print(f"[dim]No Bernstein worktrees found under {repo_root}/.sdd/.[/dim]")
        return
    console.print(render_worktrees_table(rows))
    reapable = sum(1 for r in rows if r.is_reapable)
    if reapable:
        console.print(
            f"[yellow]{reapable} worktree(s) reapable — run [bold]bernstein worktrees gc[/bold] to clean up.[/yellow]"
        )


@worktrees_group.command("gc")
@click.option(
    "--workdir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("."),
    show_default=True,
    help="Project root containing .sdd/.",
)
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
@click.option(
    "--dry",
    "dry_run",
    is_flag=True,
    default=False,
    help="Print what would be deleted without touching disk.",
)
def gc_cmd(workdir: Path, yes: bool, dry_run: bool) -> None:
    """Delete orphan, stale, and corrupt worktrees."""
    repo_root = workdir.resolve()
    rows = classify_worktrees(repo_root)
    reapable = [r for r in rows if r.is_reapable]

    console = Console()
    if not reapable:
        console.print("[green]No reapable worktrees — nothing to do.[/green]")
        return

    console.print(render_worktrees_table(reapable))
    if not yes and not dry_run and not click.confirm(f"Reap {len(reapable)} worktree(s)?", default=False):
        click.echo("Aborted.")
        raise SystemExit(1)

    try:
        run_gc(
            repo_root,
            reapable,
            dry_run=dry_run,
            on_progress=lambda row, removed: _print_reap_progress(console, row, removed, dry_run=dry_run),
        )
    except GcLockError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


def _print_reap_progress(
    console: Console,
    row: ClassifiedWorktree,
    removed: bool,
    *,
    dry_run: bool,
) -> None:
    verb = "Would remove" if dry_run else ("Removed" if removed else "Skipped")
    console.print(f"[dim]{verb}[/dim] {row.path} ({row.state.value})")


def run_gc(
    repo_root: Path,
    rows: list[ClassifiedWorktree],
    *,
    dry_run: bool,
    on_progress: Callable[[ClassifiedWorktree, bool], None] | None = None,
) -> int:
    """Reap ``rows`` under the GC lock and emit lifecycle events.

    Returns the number of worktrees actually removed (always 0 in
    ``--dry`` mode after the lock work completes).
    """
    removed_count = 0
    with lock_gc(repo_root):
        for row in rows:
            removed = reap_worktree(repo_root, row, dry_run=dry_run)
            if on_progress is not None:
                on_progress(row, removed)
            if removed and not dry_run:
                removed_count += 1
            _emit_worktree_gc(repo_root, row, dry_run)
    return removed_count
