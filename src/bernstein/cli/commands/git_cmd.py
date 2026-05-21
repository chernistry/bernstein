"""``bernstein git`` -- snapshot, undo, diff, and stacked-branch surface.

These commands are the operator-facing view of
:mod:`bernstein.core.git.snapshot`. The orchestrator captures a
snapshot before every workspace-mutating tool call; this group lets the
operator inspect, diff, and rewind those snapshots without dropping
into git plumbing.

Subcommands:

* ``bernstein git snapshots [--task <id>]`` - list recent snapshots.
* ``bernstein git undo <snapshot_id>`` - restore the work tree to that
  snapshot's tree.
* ``bernstein git diff <a> <b>`` - show ``git diff --stat`` between two
  snapshots.
* ``bernstein git stack [--task <id>]`` - render the task's stacked
  branch list, oldest first.
* ``bernstein git gc [--days N]`` - prune snapshots older than ``N``
  days (default 30, matches the documented retention policy).

The commands operate on the current working directory by default;
``--workdir`` lets the caller point at a different repo. None of them
touch the remote - snapshots live in a local side-ref namespace.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.git.snapshot import (
    DEFAULT_GC_DAYS,
    SnapshotError,
    SnapshotStore,
    stack_clear,
    stack_list,
)


def _format_ts(ts_ns: int) -> str:
    """Render a nanosecond timestamp as a short UTC string."""
    if not ts_ns:
        return "-"
    import time

    secs = ts_ns // 1_000_000_000
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(secs))


@click.group(name="git")
def git_cmd() -> None:
    """Snapshot-aware git surface for Bernstein agent runs.

    Bernstein takes a snapshot of the work tree before every tool call
    that mutates files. This group lists those snapshots and lets you
    rewind any single step without disturbing other agents' work.
    """


@git_cmd.command("snapshots")
@click.option("--task", "task_id", default=None, help="Filter to a single task ID.")
@click.option("--limit", default=50, show_default=True, help="Maximum rows to show.")
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Repository root (defaults to the current directory).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit JSON instead of the human-readable table.",
)
def snapshots_cmd(task_id: str | None, limit: int, workdir: str, as_json: bool) -> None:
    """List snapshots in newest-first order."""
    try:
        store = SnapshotStore(Path(workdir))
        snaps = store.list(task_id=task_id, limit=limit)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps([s.to_dict() for s in snaps], indent=2, sort_keys=True))
        return

    if not snaps:
        console.print("[dim]no snapshots[/dim]")
        return

    table = Table(title="Bernstein snapshots", show_lines=False)
    table.add_column("id", style="bold")
    table.add_column("timestamp")
    table.add_column("task")
    table.add_column("tool_call")
    table.add_column("agent")
    table.add_column("label")
    for snap in snaps:
        table.add_row(
            snap.snapshot_id,
            _format_ts(snap.ts_ns),
            snap.task_id or "-",
            snap.tool_call_id or "-",
            snap.agent_id or "-",
            snap.label or "-",
        )
    console.print(table)


@git_cmd.command("undo")
@click.argument("snapshot_id")
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Repository root (defaults to the current directory).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Discard uncommitted changes. Without --force we refuse a dirty undo.",
)
def undo_cmd(snapshot_id: str, workdir: str, force: bool) -> None:
    """Restore the work tree to SNAPSHOT_ID's tree."""
    try:
        store = SnapshotStore(Path(workdir))
        restored = store.undo(snapshot_id, allow_dirty=force)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(
        f"[green]restored[/green] work tree to snapshot [bold]{restored.snapshot_id}[/bold] "
        f"(tree {restored.tree_sha[:12]})"
    )


@git_cmd.command("diff")
@click.argument("a")
@click.argument("b")
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Repository root (defaults to the current directory).",
)
def diff_cmd(a: str, b: str, workdir: str) -> None:
    """Show ``git diff --stat`` between snapshots A and B."""
    try:
        store = SnapshotStore(Path(workdir))
        diff_text = store.diff(a, b)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    if not diff_text.strip():
        console.print("[dim]no changes[/dim]")
        return
    click.echo(diff_text)


@git_cmd.command("stack")
@click.option("--task", "task_id", required=True, help="Task ID whose stack to show.")
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Repository root (defaults to the current directory).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit JSON instead of the human-readable table.",
)
def stack_cmd(task_id: str, workdir: str, as_json: bool) -> None:
    """List branches for TASK_ID in chronological order (oldest first)."""
    try:
        entries = stack_list(Path(workdir), task_id=task_id)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "task_id": e.task_id,
                        "index": e.index,
                        "branch": e.branch,
                        "parent_branch": e.parent_branch,
                        "ref": e.ref,
                    }
                    for e in entries
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not entries:
        console.print(f"[dim]no stack entries for task[/dim] [bold]{task_id}[/bold]")
        return

    table = Table(title=f"Stack for task {task_id}")
    table.add_column("idx", style="bold")
    table.add_column("branch")
    table.add_column("parent")
    for entry in entries:
        table.add_row(str(entry.index), entry.branch, entry.parent_branch or "-")
    console.print(table)


@git_cmd.command("stack-clear")
@click.option("--task", "task_id", required=True, help="Task ID whose stack to clear.")
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Repository root (defaults to the current directory).",
)
def stack_clear_cmd(task_id: str, workdir: str) -> None:
    """Drop every stack entry for TASK_ID.

    Used during task archive so the ref namespace does not grow
    unbounded. Snapshots are untouched - only the stack ordering refs
    are removed.
    """
    try:
        removed = stack_clear(Path(workdir), task_id=task_id)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"[green]removed[/green] {removed} stack entries for task [bold]{task_id}[/bold]")


@git_cmd.command("gc")
@click.option(
    "--days",
    default=DEFAULT_GC_DAYS,
    show_default=True,
    help="Delete snapshots older than this many days.",
)
@click.option(
    "--workdir",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Repository root (defaults to the current directory).",
)
def gc_cmd(days: int, workdir: str) -> None:
    """Garbage-collect snapshots older than --days."""
    # Negative retention windows would push the cutoff into the future and
    # delete every snapshot, so reject them up front rather than rely on the
    # store layer.
    if days < 0:
        raise click.BadParameter("--days must be non-negative", param_hint="--days")
    try:
        store = SnapshotStore(Path(workdir))
        deleted = store.gc(older_than_days=days)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"[green]gc[/green] removed {len(deleted)} snapshot(s) older than {days}d")


__all__ = ["git_cmd"]
