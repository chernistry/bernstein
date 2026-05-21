"""``bernstein abandonments`` - inspect the agent abandonment ledger (#1350).

The ledger lives at ``<workdir>/.sdd/runtime/abandonments.jsonl`` and is
written every time an adapter calls ``ctx.abandon(reason, detail)`` or
an operator issues ``bernstein task abandon …`` (future). This CLI
exposes the read side: ``list`` for recent rows, ``stats`` for the
roll-up by reason / role / adapter.

Both commands honour the global ``--json`` flag exported by
:mod:`bernstein.cli.helpers` so the output is consumable from operator
scripts as well as from the TTY.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import click

from bernstein.cli.helpers import console, is_json, print_json
from bernstein.core.tasks.abandon import AbandonmentLedger


def _ledger(workdir: Path) -> AbandonmentLedger:
    """Resolve the ledger handle for *workdir*."""
    return AbandonmentLedger(workdir / ".sdd")


def _fmt_ts(ts: float) -> str:
    """Format an epoch second to a compact ISO-8601 UTC string."""
    if ts <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return str(ts)


@click.group("abandonments")
def abandonments_group() -> None:
    """Inspect the agent abandonment ledger (#1350)."""


@abandonments_group.command("list")
@click.option(
    "--workdir",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Project root (parent of .sdd/).",
)
@click.option("--limit", default=20, show_default=True, type=int, help="Maximum rows to display.")
@click.option("--json", "json_out", is_flag=True, default=False, help="Emit machine-readable JSON.")
def list_cmd(workdir: Path, limit: int, json_out: bool) -> None:
    """List the most recent abandonment rows (newest first)."""
    rows = _ledger(workdir).list_recent(limit=limit)
    if json_out or is_json():
        print_json([row.to_dict() for row in rows])
        return
    if not rows:
        console.print("[dim]No abandonments recorded.[/dim]")
        return
    # Lazy import so the Rich table render stays optional.
    from rich.table import Table

    table = Table(title="Recent abandonments")
    table.add_column("timestamp", style="dim")
    table.add_column("task_id", style="bold")
    table.add_column("role")
    table.add_column("reason", style="yellow")
    table.add_column("adapter")
    table.add_column("attempts", justify="right")
    table.add_column("detail", overflow="fold")
    for row in rows:
        table.add_row(
            _fmt_ts(row.timestamp),
            row.task_id,
            row.role,
            row.reason.value,
            row.adapter,
            str(row.attempts),
            row.detail,
        )
    console.print(table)


@abandonments_group.command("stats")
@click.option(
    "--workdir",
    default=".",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Project root (parent of .sdd/).",
)
@click.option("--json", "json_out", is_flag=True, default=False, help="Emit machine-readable JSON.")
def stats_cmd(workdir: Path, json_out: bool) -> None:
    """Show abandonment roll-ups by reason / role / adapter."""
    stats = _ledger(workdir).stats()
    if json_out or is_json():
        print_json(stats)
        return
    if stats["total"] == 0:
        console.print("[dim]No abandonments recorded.[/dim]")
        return
    console.print(f"[bold]Total abandonments:[/bold] {stats['total']}")
    for label, key in (("By reason", "by_reason"), ("By role", "by_role"), ("By adapter", "by_adapter")):
        body = stats[key]
        if not body:
            continue
        console.print(f"\n[bold]{label}[/bold]")
        for name, count in sorted(body.items(), key=lambda kv: (-kv[1], kv[0])):
            console.print(f"  {name}: {count}")


def render_rows_jsonl(rows_path: Path) -> str:
    """Return the raw JSONL ledger contents for snapshot tests.

    Wraps :meth:`Path.read_text` so test fixtures can assert against a
    deterministic byte sequence without re-implementing the read.
    """
    if not rows_path.exists():
        return ""
    return rows_path.read_text(encoding="utf-8")


def render_rows_table_text(rows: list[dict[str, object]]) -> str:
    """Render a minimal text table for snapshot tests.

    The Rich Table renderer is non-deterministic across terminal widths,
    so snapshots use this canonical, width-independent format.
    """
    if not rows:
        return "(empty)\n"
    lines = ["timestamp\ttask_id\trole\treason\tadapter\tattempts\tdetail"]
    for row in rows:
        raw_ts = row.get("timestamp", 0.0)
        ts = float(raw_ts) if isinstance(raw_ts, (int, float, str)) else 0.0
        lines.append(
            "\t".join(
                [
                    _fmt_ts(ts),
                    str(row.get("task_id", "")),
                    str(row.get("role", "")),
                    str(row.get("reason", "")),
                    str(row.get("adapter", "")),
                    str(row.get("attempts", 0)),
                    str(row.get("detail", "")),
                ]
            )
        )
    return "\n".join(lines) + "\n"


__all__ = ["abandonments_group", "render_rows_jsonl", "render_rows_table_text"]
