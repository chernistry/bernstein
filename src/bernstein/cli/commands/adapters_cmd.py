"""``bernstein adapters check`` - conformance + capability report.

Surfaces the same data the conformance pytest suite covers, but with no
pytest subprocess and an operator-friendly Rich table on stdout. JSON
output is wired through ``--format json`` and consumed by CI dashboards.

The command attaches to the pre-existing ``adapters`` group declared in
:mod:`bernstein.cli.commands.adapter_cmd`. The wiring lives in
:func:`register_adapters_check` so the main CLI module can call it
once without exposing import-order quirks.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from bernstein.adapters.report import (
    CONFORMANCE_FAIL,
    CONFORMANCE_OK,
    AdapterStatus,
    build_report,
)
from bernstein.cli.helpers import console

if TYPE_CHECKING:
    from pathlib import Path

    from bernstein.adapters.report import AdapterReport

# ``check`` output formats. Public so tests can reference the choices.
FORMAT_TABLE = "table"
FORMAT_JSON = "json"
FORMAT_CHOICES = (FORMAT_TABLE, FORMAT_JSON)


def _format_capabilities(caps: frozenset[str]) -> str:
    """Compact, deterministic capability list for the table column."""
    if not caps:
        return "-"
    return ",".join(sorted(caps))


def _format_conformance(verdict: str) -> str:
    """Colourize a verdict for the Rich table."""
    if verdict == CONFORMANCE_OK:
        return f"[green]{verdict}[/green]"
    if verdict == CONFORMANCE_FAIL:
        return f"[red]{verdict}[/red]"
    return f"[yellow]{verdict}[/yellow]"


def _render_table_rows(report: AdapterReport) -> str:
    """Render the Rich-coloured table to a deterministic string.

    Splits the heavy lifting out of the Click command so unit tests can
    snapshot the output without spinning up a TTY.
    """
    from io import StringIO

    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"Bernstein adapter report ({report.summary.total})", show_lines=False)
    table.add_column("adapter", style="cyan", no_wrap=True)
    table.add_column("binary", style="white")
    table.add_column("version", style="dim")
    table.add_column("caps", style="white")
    table.add_column("conformance", style="bold")
    table.add_column("notes", style="dim")

    for row in report.adapters:
        binary = row.binary_resolved or "[yellow](not in PATH)[/yellow]"
        version = row.version_string or "[dim](n/a)[/dim]"
        caps = _format_capabilities(row.capabilities)
        verdict = _format_conformance(row.conformance)
        notes = row.conformance_detail or ""
        table.add_row(row.name, binary, version, caps, verdict, notes)

    buf = StringIO()
    sub_console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    sub_console.print(table)
    footer = (
        f"\n{report.summary.total} adapters total"
        f" - {report.summary.reachable} reachable"
        f" - {report.summary.conform} conform"
        f" - {report.summary.fail} fail"
        f" - {report.summary.skip} skip"
    )
    sub_console.print(footer)
    return buf.getvalue()


def _render_list_rows(report: AdapterReport) -> str:
    """One-line status per adapter for ``bernstein adapters check --list``."""
    from io import StringIO

    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"Bernstein adapters ({report.summary.total})", show_lines=False)
    table.add_column("adapter", style="cyan", no_wrap=True)
    table.add_column("module", style="dim")
    table.add_column("binary", style="white")
    table.add_column("conformance", style="bold")

    for row in report.adapters:
        binary = row.binary_resolved or "[yellow]missing[/yellow]"
        table.add_row(row.name, row.module_path, binary, _format_conformance(row.conformance))

    buf = StringIO()
    sub_console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    sub_console.print(table)
    return buf.getvalue()


def _emit_report(
    report: AdapterReport,
    *,
    output_format: str,
    list_mode: bool,
) -> None:
    """Push the report to the console in the requested format."""
    if output_format == FORMAT_JSON:
        click.echo(report.to_json())
        return
    if list_mode:
        console.print(_render_list_rows(report), end="")
    else:
        console.print(_render_table_rows(report), end="")


def _exit_code(report: AdapterReport, *, strict: bool) -> int:
    """Decide the process exit code for ``bernstein adapters check``."""
    if strict and report.summary.fail > 0:
        return 1
    return 0


def _execute_check(
    name: str | None,
    *,
    output_format: str,
    strict: bool,
    list_mode: bool,
    contracts_dir: Path | None,
    capture_version: bool,
) -> int:
    """Build the report and emit it; return the process exit code.

    Split out from the Click command so unit + integration tests can
    exercise the full pipeline without invoking ``click.testing``.
    """
    try:
        report = build_report(
            contracts_dir=contracts_dir,
            capture_version=capture_version,
            only=name,
        )
    except KeyError:
        click.echo(f"Unknown adapter: {name!r}", err=True)
        return 2

    _emit_report(report, output_format=output_format, list_mode=list_mode)
    return _exit_code(report, strict=strict)


@click.command("check")
@click.argument("name", required=False)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(list(FORMAT_CHOICES)),
    default=FORMAT_TABLE,
    show_default=True,
    help="Output format - rich table for humans, json for CI dashboards.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit non-zero when any adapter has conformance==fail.",
)
def adapters_check_cmd(name: str | None, output_format: str, strict: bool) -> None:
    """Run the adapter conformance + capability report.

    With no NAME, reports on every registered adapter. With NAME, only
    that adapter is reported on. ``--format json`` emits a stable JSON
    document keyed on ``adapters`` and ``summary``.
    """
    rc = _execute_check(
        name,
        output_format=output_format,
        strict=strict,
        list_mode=False,
        contracts_dir=None,
        capture_version=True,
    )
    sys.exit(rc)


@click.command("list-status")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(list(FORMAT_CHOICES)),
    default=FORMAT_TABLE,
    show_default=True,
    help="Output format - rich table for humans, json for CI dashboards.",
)
def adapters_list_status_cmd(output_format: str) -> None:
    """One-line conformance status per adapter (compact view of ``check``)."""
    rc = _execute_check(
        None,
        output_format=output_format,
        strict=False,
        list_mode=True,
        contracts_dir=None,
        capture_version=False,
    )
    sys.exit(rc)


def register_adapters_check(group: click.Group) -> None:
    """Attach the conformance subcommands to an existing ``adapters`` group.

    Called from :mod:`bernstein.cli.commands.adapter_cmd` so the new
    surface lands at ``bernstein adapters check`` without duplicating
    the group definition.
    """
    group.add_command(adapters_check_cmd, "check")
    group.add_command(adapters_list_status_cmd, "list-status")


__all__ = [
    "FORMAT_CHOICES",
    "FORMAT_JSON",
    "FORMAT_TABLE",
    "AdapterStatus",
    "adapters_check_cmd",
    "adapters_list_status_cmd",
    "register_adapters_check",
]
