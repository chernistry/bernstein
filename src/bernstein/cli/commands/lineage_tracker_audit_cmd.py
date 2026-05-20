"""``bernstein lineage tracker-audit`` -- tracker state-move audit log.

Operator surface for the signed tracker-action JSONL maintained by
:mod:`bernstein.core.lineage.tracker_audit`. Three subcommands:

* ``show`` -- chronological view filtered by tracker / ticket / since.
* ``export`` -- write a signed JSONL bundle for an auditor.
* ``verify`` -- chain + HMAC integrity check; exits non-zero on
  tampering.

The operator HMAC secret is loaded from
``BERNSTEIN_OPERATOR_SECRET`` by default; pass ``--secret-env`` to
override.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.lineage.tracker_audit import (
    DEFAULT_LOG_PATH,
    TrackerAuditLog,
)


def _load_key(secret_env: str) -> bytes:
    secret = os.environ.get(secret_env)
    if not secret:
        console.print(
            f"[red]Operator secret not set in ${secret_env}.[/red] Export the HMAC key before running this command."
        )
        sys.exit(2)
    return secret.encode("utf-8")


@click.group(name="tracker-audit")
def tracker_audit_cmd() -> None:
    """Signed audit log of tracker state moves.

    \b
    Examples:
      bernstein lineage tracker-audit show --tracker jira --ticket PROJ-1
      bernstein lineage tracker-audit export --output /tmp/bundle.jsonl
      bernstein lineage tracker-audit verify
    """


@tracker_audit_cmd.command(name="show")
@click.option(
    "--log",
    "log_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LOG_PATH,
    show_default=True,
    help="Tracker-audit JSONL path.",
)
@click.option("--tracker", "tracker_name", default=None, help="Filter by tracker name.")
@click.option("--ticket", "ticket_id", default=None, help="Filter by ticket id.")
@click.option(
    "--since",
    "since_ns",
    type=int,
    default=None,
    help="Earliest ``ts_ns`` (nanoseconds since epoch) to include.",
)
@click.option(
    "--until",
    "until_ns",
    type=int,
    default=None,
    help="Latest ``ts_ns`` to include.",
)
@click.option(
    "--secret-env",
    default="BERNSTEIN_OPERATOR_SECRET",
    show_default=True,
    help="Env var holding the HMAC operator secret.",
)
def show_cmd(
    log_path: Path,
    tracker_name: str | None,
    ticket_id: str | None,
    since_ns: int | None,
    until_ns: int | None,
    secret_env: str,
) -> None:
    """Show audit entries chronologically with optional filters."""
    key = _load_key(secret_env)
    entries = TrackerAuditLog(log_path, hmac_key=key).filter(
        tracker_name=tracker_name,
        ticket_id=ticket_id,
        since_ns=since_ns,
        until_ns=until_ns,
    )
    if not entries:
        console.print("[yellow]No entries match.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("ts_ns", style="dim", no_wrap=True)
    table.add_column("Tracker", no_wrap=True)
    table.add_column("Ticket", no_wrap=True)
    table.add_column("Action", no_wrap=True)
    table.add_column("Role", no_wrap=True)
    table.add_column("Model", no_wrap=True)
    table.add_column("Cost USD", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Entry", no_wrap=True)
    for entry in entries:
        table.add_row(
            str(entry.ts_ns),
            entry.tracker_name,
            entry.ticket_id,
            entry.action,
            entry.actor.role,
            entry.actor.model,
            f"{entry.cost_usd:.4f}",
            str(entry.tokens_in + entry.tokens_out),
            entry.entry_hash[:18] + "...",
        )
    console.print(table)


@tracker_audit_cmd.command(name="export")
@click.option(
    "--log",
    "log_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LOG_PATH,
    show_default=True,
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Destination JSONL path for the signed bundle.",
)
@click.option("--tracker", "tracker_name", default=None)
@click.option("--ticket", "ticket_id", default=None)
@click.option("--since", "since_ns", type=int, default=None)
@click.option("--until", "until_ns", type=int, default=None)
@click.option(
    "--secret-env",
    default="BERNSTEIN_OPERATOR_SECRET",
    show_default=True,
)
def export_cmd(
    log_path: Path,
    output_path: Path,
    tracker_name: str | None,
    ticket_id: str | None,
    since_ns: int | None,
    until_ns: int | None,
    secret_env: str,
) -> None:
    """Write a filtered signed JSONL bundle for an auditor."""
    key = _load_key(secret_env)
    n = TrackerAuditLog(log_path, hmac_key=key).export_bundle(
        output_path,
        tracker_name=tracker_name,
        ticket_id=ticket_id,
        since_ns=since_ns,
        until_ns=until_ns,
    )
    console.print(f"[green]Wrote {n} entry(ies) -> [/green]{output_path}")


@tracker_audit_cmd.command(name="verify")
@click.option(
    "--log",
    "log_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_LOG_PATH,
    show_default=True,
)
@click.option(
    "--secret-env",
    default="BERNSTEIN_OPERATOR_SECRET",
    show_default=True,
)
def verify_cmd(log_path: Path, secret_env: str) -> None:
    """Verify chain integrity + HMAC. Exits non-zero on tampering."""
    key = _load_key(secret_env)
    result = TrackerAuditLog(log_path, hmac_key=key).verify()
    if result.ok:
        console.print(f"[green]tracker-audit verify:[/green] OK ({result.entry_count} entry(ies))")
        return
    console.print("[red]tracker-audit verify:[/red] FAIL")
    for f in result.failures:
        console.print(f"  - {f}")
    sys.exit(1)


__all__ = ["tracker_audit_cmd"]
