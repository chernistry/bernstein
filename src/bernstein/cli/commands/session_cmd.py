"""Session command group — record, replay, and fork deterministic run sessions.

Every ``bernstein run`` that completes task planning records a session to
``.sdd/runtime/sessions/<session_id>.json``.  The session captures the
goal, a random seed, and the full task list so that the exact same run can
be reproduced at any time.

``bernstein session fork`` (#1222) clones a recorded session into a sibling
git worktree branched from the parent's current commit so an operator can
explore an alternate path without disturbing the parent.

Usage::

    bernstein session list                        # list recorded sessions
    bernstein session show 20240101-120000-abc123 # inspect a session
    bernstein session replay 20240101-120000-abc123  # re-run a session
    bernstein session replay --dry-run <session_id>  # preview
    bernstein session fork <session_id> --label use-yaml  # sibling worktree
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from bernstein.cli.helpers import console
from bernstein.core.run_session import RunSession, sessions_dir_for


@click.group("session")
def session_group() -> None:
    """Manage deterministic run sessions for reproducibility."""


@session_group.command("list")
def session_list() -> None:
    """List all recorded sessions, newest first."""
    workdir = Path.cwd()
    sdir = sessions_dir_for(workdir)
    ids = RunSession.list_sessions(sdir)
    if not ids:
        console.print(f"[dim]No sessions found in {sdir}[/dim]")
        return
    console.print(f"[bold]Recorded sessions[/bold] ({len(ids)} total):")
    for sid in ids:
        try:
            session = RunSession.load(sdir, sid)
            console.print(
                f"  [cyan]{sid}[/cyan]  "
                f"[dim]{session.created_at}[/dim]  "
                f"[green]{len(session.tasks)} tasks[/green]  "
                f"{session.goal[:60]}"
            )
        except (FileNotFoundError, ValueError):
            console.print(f"  [yellow]{sid}[/yellow]  [red](unreadable)[/red]")


@session_group.command("show")
@click.argument("session_id")
def session_show(session_id: str) -> None:
    """Show full details of a recorded session."""
    workdir = Path.cwd()
    sdir = sessions_dir_for(workdir)
    try:
        session = RunSession.load(sdir, session_id)
    except FileNotFoundError:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise SystemExit(1) from None
    except ValueError as exc:
        console.print(f"[red]Failed to load session:[/red] {exc}")
        raise SystemExit(1) from None

    console.print(f"[bold]Session:[/bold] {session.session_id}")
    console.print(f"[bold]Goal:[/bold]    {session.goal}")
    console.print(f"[bold]Seed:[/bold]    {session.run_seed}")
    console.print(f"[bold]Created:[/bold] {session.created_at}")
    console.print(f"[bold]Git SHA:[/bold] {session.git_sha or '(not recorded)'}")
    console.print(f"[bold]Version:[/bold] {session.bernstein_version or '(not recorded)'}")
    console.print(f"\n[bold]Tasks[/bold] ({len(session.tasks)}):")
    for i, task in enumerate(session.tasks, 1):
        console.print(f"  {i:2}. [{task.get('role', '?')}] {task.get('title', '(untitled)')[:70]}")
    if session.routing_decisions:
        console.print(f"\n[bold]Routing decisions:[/bold] {json.dumps(session.routing_decisions, indent=2)}")


@session_group.command("replay")
@click.argument("session_id", required=False, default=None)
@click.option(
    "--port",
    default=8052,
    show_default=True,
    help="Port for the task server.",
)
@click.option(
    "--cli",
    default=None,
    type=click.Choice(["auto", "claude", "codex", "gemini", "aider", "qwen"], case_sensitive=False),
    help="Force specific CLI agent (overrides session default).",
)
@click.option(
    "--model",
    default=None,
    help="Force specific model (overrides session default).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the tasks that would execute without actually running them.",
)
def session_replay(
    session_id: str | None,
    port: int,
    cli: str | None,
    model: str | None,
    dry_run: bool,
) -> None:
    """Replay a recorded session for deterministic reproducibility.

    \b
      bernstein session replay 20240101-120000-abc123     # re-run
      bernstein session replay                            # replay latest
      bernstein session replay --dry-run <session_id>    # preview tasks
    """
    workdir = Path.cwd()
    sdir = sessions_dir_for(workdir)

    if not session_id:
        ids = RunSession.list_sessions(sdir)
        if not ids:
            console.print("[red]No recorded sessions found.[/red]")
            console.print(f"[dim]Sessions directory:[/dim] {sdir}")
            raise SystemExit(1)
        console.print(f"[yellow]SESSION_ID not provided: replaying latest:[/yellow] {ids[0]}")
        session_id = ids[0]

    try:
        session = RunSession.load(sdir, session_id)
    except FileNotFoundError:
        console.print(f"[red]Session not found:[/red] {session_id}")
        raise SystemExit(1) from None
    except ValueError as exc:
        console.print(f"[red]Failed to load session:[/red] {exc}")
        raise SystemExit(1) from None

    console.print(f"[green]Replaying session:[/green] {session.session_id}")
    console.print(f"[dim]Goal:[/dim]  {session.goal}")
    console.print(f"[dim]Seed:[/dim]  {session.run_seed}")
    console.print(f"[dim]Tasks:[/dim] {len(session.tasks)}")

    # Re-apply the same random seed for deterministic routing decisions
    session.apply_seed()

    # Deserialise tasks back to Task objects
    tasks = session.to_tasks()

    if dry_run:
        console.print("\n[yellow]Dry run: tasks that would execute:[/yellow]")
        for i, task in enumerate(tasks, 1):
            console.print(f"  {i:2}. [{task.role}] {task.title[:70]}")  # type: ignore[union-attr]
        return

    from bernstein.core.bootstrap import (  # pyright: ignore[reportUnknownVariableType]
        bootstrap_from_goal,
    )

    try:
        bootstrap_from_goal(
            goal=session.goal,
            workdir=workdir,
            port=port,
            cli=cli or "auto",
            model=model,
            tasks=tasks,  # type: ignore[arg-type]
        )
    except RuntimeError as exc:
        console.print(f"[red]Replay failed:[/red] {exc}")
        raise SystemExit(1) from exc

    console.print("[green]Replay complete.[/green]")


@session_group.command("fork")
@click.argument("session_id")
@click.option(
    "--label",
    "fork_label",
    default="",
    show_default=False,
    help="Short label baked into the fork branch / session id (a-z, 0-9, '.-_').",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the fork descriptor as JSON instead of human-readable output.",
)
def session_fork(session_id: str, fork_label: str, as_json: bool) -> None:
    """Fork a recorded session into a sibling git worktree.

    Creates a new worktree under ``.sdd/worktrees/<fork_session_id>``
    branched from the parent's current HEAD commit and writes a snapshot
    of the parent's task state into the fork's sessions directory.  The
    parent session is untouched.

    Example::

        bernstein session fork 20240101-120000-abc123 --label use-yaml
    """
    from bernstein.core.sessions.fork import SessionForkError, fork_session

    workdir = Path.cwd()
    try:
        fork = fork_session(
            parent_session_id=session_id,
            fork_label=fork_label,
            repo_root=workdir,
        )
    except SessionForkError as exc:
        console.print(f"[red]Fork failed:[/red] {exc}")
        raise SystemExit(1) from exc

    if as_json:
        click.echo(json.dumps(fork.to_dict(), indent=2, sort_keys=True))
        return

    console.print("[green]Forked session.[/green]")
    console.print(f"  [bold]parent:[/bold]    {fork.parent_session_id}")
    console.print(f"  [bold]fork:[/bold]      {fork.fork_session_id}")
    console.print(f"  [bold]branch:[/bold]    {fork.fork_branch}")
    console.print(f"  [bold]worktree:[/bold]  {fork.fork_worktree}")
    console.print(f"  [bold]commit:[/bold]    {fork.fork_commit[:12]}")
    console.print(f"  [bold]snapshot:[/bold]  {fork.snapshot_path}")
