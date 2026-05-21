"""CLI command group: ``bernstein decisions`` - inspect the decision log.

Subcommands:

* ``tail`` - print the most recent decisions (default last 20).
* ``search`` - filter by ``--kind`` and/or ``--since <duration>``.

The ledger lives at ``.sdd/runtime/decisions.jsonl`` (override with
``--path``). Records are produced by
:mod:`bernstein.core.observability.decision_log`.
"""

from __future__ import annotations

import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from bernstein.core.observability.decision_log import (
    DEFAULT_PATH,
    DecisionRecord,
    filter_by_kind,
    filter_since,
    parse_duration,
    replay,
)


def _render(records: list[DecisionRecord], console: Console | None = None) -> str:
    """Render *records* as a Rich table and return the captured plain text.

    A captured Console lets us return a deterministic string for snapshot
    tests while still pretty-printing for the interactive operator.
    """
    if console is None:
        console = Console(record=True, width=120)
    table = Table(title="Decisions", show_header=True, header_style="bold")
    table.add_column("ts", style="dim", no_wrap=True)
    table.add_column("kind")
    table.add_column("chosen")
    table.add_column("conf", justify="right")
    table.add_column("rationale", overflow="fold")
    for r in records:
        table.add_row(
            f"{r.ts:.3f}",
            r.kind,
            r.chosen,
            f"{r.confidence:.2f}",
            r.rationale[:80],
        )
    console.print(table)
    return console.export_text()


@click.group("decisions")
def decisions_group() -> None:
    """Inspect the structured decision log.

    \b
    Examples:
      bernstein decisions tail
      bernstein decisions tail -n 50
      bernstein decisions search --kind model_route --since 1h
    """


@decisions_group.command("tail")
@click.option(
    "-n",
    "--lines",
    default=20,
    show_default=True,
    type=int,
    help="Maximum number of recent decisions to print.",
)
@click.option(
    "--path",
    "path_str",
    default=None,
    help="Override the JSONL path (defaults to .sdd/runtime/decisions.jsonl).",
)
def decisions_tail(lines: int, path_str: str | None) -> None:
    """Print the most recent decisions.

    Records are read from the JSONL ledger and rendered as a table.
    """
    path = Path(path_str) if path_str else DEFAULT_PATH
    records = replay(path)
    if lines > 0:
        records = records[-lines:]
    _render(records)


@decisions_group.command("search")
@click.option("--kind", "kind", default=None, help="Filter by decision kind.")
@click.option(
    "--since",
    "since",
    default=None,
    help='Duration filter, e.g. "30s", "15m", "2h", "1d".',
)
@click.option(
    "--path",
    "path_str",
    default=None,
    help="Override the JSONL path (defaults to .sdd/runtime/decisions.jsonl).",
)
def decisions_search(kind: str | None, since: str | None, path_str: str | None) -> None:
    """Search decisions by kind and/or time window."""
    path = Path(path_str) if path_str else DEFAULT_PATH
    records = replay(path)
    if kind is not None:
        records = filter_by_kind(records, kind)
    if since is not None:
        seconds = parse_duration(since)
        cutoff = time.time() - seconds
        records = filter_since(records, cutoff)
    _render(records)
