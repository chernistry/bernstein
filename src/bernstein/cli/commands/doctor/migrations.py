"""``bernstein doctor migrations`` -- on-disk state migration surface.

Lists the migrations applied to the project's ``.sdd`` state directory and
those still pending, and reports the current schema version stamp. Read-only
by default; pass ``--apply`` to run pending migrations forward.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import click

from bernstein.cli.helpers import console


def _sdd_dir(workdir: Path | None = None) -> Path:
    """Resolve the project ``.sdd`` directory."""
    return (workdir or Path.cwd()) / ".sdd"


@click.command("migrations")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of the Rich table.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    default=False,
    help="Apply pending migrations forward (default is read-only).",
)
def migrations_cmd(as_json: bool, do_apply: bool) -> None:
    """List applied and pending on-disk state migrations.

    \b
    Examples:
      bernstein doctor migrations          # list applied / pending
      bernstein doctor migrations --json   # machine-readable output
      bernstein doctor migrations --apply  # run pending migrations
    """
    from bernstein.core.persistence.migrations import (
        EXIT_FUTURE_VERSION,
        FutureSchemaVersionError,
        applied_migrations,
        latest_version,
        migrate,
        pending_migrations,
        read_schema_version,
    )

    sdd = _sdd_dir()

    if do_apply:
        try:
            migrate(sdd)
        except FutureSchemaVersionError as exc:
            console.print(f"[red]error[/red]: {exc}")
            raise SystemExit(EXIT_FUTURE_VERSION) from exc

    current = read_schema_version(sdd)
    latest = latest_version()
    applied = applied_migrations(sdd)
    pending = pending_migrations(sdd)

    if as_json:
        payload = {
            "schema_version": current,
            "latest_version": latest,
            "applied": [{"version": m.version, "description": m.description} for m in applied],
            "pending": [{"version": m.version, "description": m.description} for m in pending],
        }
        console.print_json(_json.dumps(payload))
        raise SystemExit(0)

    from rich.table import Table

    console.print(f"[bold]doctor migrations[/bold]: schema v{current} (latest known v{latest})")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Version")
    table.add_column("State")
    table.add_column("Description")
    for m in applied:
        table.add_row(f"v{m.version:03d}", "[green]applied[/green]", m.description)
    for m in pending:
        table.add_row(f"v{m.version:03d}", "[yellow]pending[/yellow]", m.description)
    if not applied and not pending:
        table.add_row("-", "[dim]none[/dim]", "no migrations registered")
    console.print(table)

    if pending:
        console.print(
            f"[yellow]{len(pending)} pending[/yellow]. Run `bernstein doctor migrations --apply` to migrate forward."
        )

    raise SystemExit(0)


def register(parent: click.Group) -> None:
    """Attach the migrations subcommand to the parent ``doctor`` group."""

    parent.add_command(migrations_cmd)
