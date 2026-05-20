"""``bernstein desktop-register`` -- register Bernstein into host apps.

Registers Bernstein's MCP server into a host application's config so the
host auto-discovers Bernstein's tools without manual wiring. Two hosts are
supported today (Claude Desktop, Claude Code); the rest are listed as
stubbed via ``--list`` so the surface is discoverable.
"""

from __future__ import annotations

import json as _json

import click
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.substrate.host_registry import (
    HOST_REGISTRY,
    HostStatus,
    known_host_names,
)
from bernstein.core.substrate.register import is_registered, register_host


def _list_payload() -> list[dict[str, object]]:
    """Build the per-host status rows used by ``--list`` (table and JSON)."""
    rows: list[dict[str, object]] = []
    for name in known_host_names():
        host = HOST_REGISTRY[name]
        path = host.config_path()
        registered = is_registered(host, path=path) if host.supported else False
        rows.append(
            {
                "host": host.name,
                "display_name": host.display_name,
                "status": host.status.value,
                "scope": host.scope,
                "config_path": str(path) if path is not None else None,
                "registered": registered,
                "notes": host.notes,
            }
        )
    return rows


def _render_list(*, as_json: bool) -> None:
    rows = _list_payload()
    if as_json:
        console.print_json(_json.dumps({"hosts": rows}))
        return

    table = Table(title="Bernstein substrate hosts", show_lines=False)
    table.add_column("Host", style="cyan", no_wrap=True)
    table.add_column("Status")
    table.add_column("Registered")
    table.add_column("Config path", overflow="fold")

    for row in rows:
        if row["status"] == HostStatus.SUPPORTED.value:
            status_cell = "[green]supported[/green]"
            reg_cell = "[green]yes[/green]" if row["registered"] else "[yellow]no[/yellow]"
        else:
            status_cell = "[dim]stubbed[/dim]"
            reg_cell = "[dim]-[/dim]"
        path = row["config_path"] or "[dim](n/a on this OS)[/dim]"
        table.add_row(str(row["host"]), status_cell, reg_cell, str(path))

    console.print(table)
    console.print("\n[dim]Supported hosts can be registered with[/dim] bernstein desktop-register --host <name>")


@click.command("desktop-register")
@click.option("--host", "host_name", default=None, help="Host to register Bernstein into (e.g. claude-desktop).")
@click.option(
    "--list",
    "do_list",
    is_flag=True,
    default=False,
    help="List supported/stubbed hosts and registration state.",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
def desktop_register_cmd(host_name: str | None, do_list: bool, as_json: bool) -> None:
    """Register Bernstein's MCP server into a host application.

    \b
    Examples:
      bernstein desktop-register --list
      bernstein desktop-register --host claude-desktop
      bernstein desktop-register --host claude-code
    """
    if do_list and host_name is not None:
        raise click.UsageError("Use either --list or --host <name>, not both.")

    if do_list or host_name is None:
        if host_name is None and not do_list:
            console.print("[yellow]Specify --host <name> or --list.[/yellow]\n")
        _render_list(as_json=as_json)
        if host_name is None and not do_list:
            raise SystemExit(2)
        return

    host = HOST_REGISTRY.get(host_name)
    if host is None:
        valid = ", ".join(known_host_names())
        raise click.BadParameter(f"unknown host {host_name!r}; known hosts: {valid}", param_hint="--host")

    if not host.supported:
        console.print(
            f"[yellow]Host '{host.display_name}' is not yet supported for registration.[/yellow]\n"
            f"[dim]{host.notes}[/dim]"
        )
        raise SystemExit(1)

    result = register_host(host)

    if as_json:
        console.print_json(
            _json.dumps(
                {
                    "host": result.host,
                    "action": result.action,
                    "config_path": str(result.config_path),
                    "backup_path": str(result.backup_path) if result.backup_path else None,
                }
            )
        )
        return

    if result.action == "already_registered":
        console.print(f"[green]Bernstein is already registered in {host.display_name}.[/green]")
        console.print(f"[dim]Config:[/dim] {result.config_path}")
        return

    verb = "Updated" if result.action == "updated" else "Registered"
    console.print(f"[green]{verb} Bernstein in {host.display_name}.[/green]")
    console.print(f"[dim]Config:[/dim] {result.config_path}")
    if result.backup_path:
        console.print(f"[dim]Backup:[/dim] {result.backup_path}")
    if host.notes:
        console.print(f"[cyan]Next:[/cyan] {host.notes}")


__all__ = ["desktop_register_cmd"]
