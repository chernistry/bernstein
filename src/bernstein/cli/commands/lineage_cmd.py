"""``bernstein lineage`` -- per-artifact lineage trail commands.

Two surfaces:

* ``bernstein lineage <file>:<line>`` (legacy positional form) -- walks
  the lineage chain back from a file/line to the producing agent. This
  invocation existed before the regulator-class extension and is kept
  for back-compat.
* ``bernstein lineage walk <file>:<line>`` -- explicit form of the
  above; preferred in scripts to avoid colliding with subcommand names.
* ``bernstein lineage export <run_id> --format <csv|jsonld|html>`` --
  produce a regulator-shaped artefact for an audit package.
* ``bernstein lineage verify <run_id>`` -- one-shot chain verification;
  exits 0 only when every record validates.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from bernstein.cli.commands.lineage_export_cmd import lineage_export_cmd
from bernstein.cli.commands.lineage_verify_cmd import lineage_verify_cmd
from bernstein.cli.helpers import console


def _parse_target(target: str) -> tuple[str, int | None]:
    """Split ``"path/to/file.py:42"`` into ``("path/to/file.py", 42)``.

    A bare path returns ``(path, None)`` -- the lookup then matches
    every record for the file regardless of line.
    """
    if ":" not in target:
        return target, None
    path, _, suffix = target.rpartition(":")
    if not path:
        return target, None
    try:
        line = int(suffix)
    except ValueError:
        return target, None
    return path, line


class _LineageGroup(click.Group):
    """Group that preserves the legacy ``bernstein lineage <file>:<line>`` form.

    Without this override, ``bernstein lineage src/foo.py:42`` would
    fail with ``No such command 'src/foo.py:42'``. We rewrite the
    args so click-internally invokes the ``walk`` subcommand whenever
    the first positional token is not a registered subcommand name.
    """

    def resolve_command(
        self,
        ctx: click.Context,
        args: list[str],
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = ["walk", *args]
        return super().resolve_command(ctx, args)


@click.group(name="lineage", cls=_LineageGroup, invoke_without_command=True)
@click.pass_context
def lineage_cmd(ctx: click.Context) -> None:
    """Per-artifact lineage trail (output -> producer + inputs).

    \b
    Examples:
      bernstein lineage src/foo.py:42
      bernstein lineage walk src/foo.py:42
      bernstein lineage export <run_id> --format html --output /tmp/x.html
      bernstein lineage verify <run_id> --public-key /etc/customer-pub.pem
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@lineage_cmd.command(name="walk")
@click.argument("target", required=True)
@click.option(
    "--workdir",
    "-w",
    type=click.Path(file_okay=False, exists=True),
    default=".",
    show_default=True,
    help="Project root containing .sdd/.",
)
@click.option(
    "--run",
    "run_id",
    default=None,
    help="Restrict to a single run id (default: all runs in the WAL directory).",
)
@click.option(
    "--limit",
    type=int,
    default=20,
    show_default=True,
    help="Maximum number of records to display.",
)
def walk_cmd(target: str, workdir: str, run_id: str | None, limit: int) -> None:
    """Walk the lineage chain backwards from ``<file>[:<line>]``."""
    from bernstein.core.persistence.lineage import LineageReader

    sdd_dir = Path(workdir).resolve() / ".sdd"
    if not sdd_dir.is_dir():
        console.print(f"[red]No .sdd directory at[/red] {sdd_dir}")
        raise SystemExit(1)

    path, line = _parse_target(target)

    reader = LineageReader(sdd_dir)
    records = reader.lookup(path, line, run_id=run_id)

    if not records:
        console.print(f"[yellow]No lineage records for[/yellow] {target}")
        return

    records = records[-limit:]

    where = f"{path}:{line}" if line is not None else path
    console.print()
    console.print(f"[bold]Lineage trail for[/bold] {where} ({len(records)} record(s))")
    console.print()

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Producer", no_wrap=True)
    table.add_column("Run", no_wrap=True)
    table.add_column("Prompt SHA", style="dim", no_wrap=True)
    table.add_column("Inputs", overflow="fold")
    table.add_column("Model", no_wrap=True)
    table.add_column("Tokens", justify="right")
    table.add_column("Cost USD", justify="right")
    table.add_column("Reg. class", no_wrap=True)
    table.add_column("Cust. sig", no_wrap=True)

    for record in records:
        ts = f"{record.timestamp:.0f}" if record.timestamp else "-"
        inputs_str = ", ".join(a.path for a in record.inputs) or "-"
        prompt_short = record.prompt_sha[:12] + "..." if record.prompt_sha else "-"
        sig_short = "yes" if record.customer_signature else "-"
        table.add_row(
            ts,
            record.producer.agent_id,
            record.producer.run_id,
            prompt_short,
            inputs_str,
            record.model or "-",
            str(record.tokens),
            f"{record.cost_usd:.4f}",
            record.regulatory_class or "-",
            sig_short,
        )

    console.print(table)
    console.print()


lineage_cmd.add_command(lineage_export_cmd, "export")
lineage_cmd.add_command(lineage_verify_cmd, "verify")
