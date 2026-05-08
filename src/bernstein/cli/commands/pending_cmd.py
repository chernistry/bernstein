"""``bernstein pending`` — list outstanding human-approval gates.

Surfaces two distinct queues:

* **Approval-pending** (#1110): tasks paused at the pre-spawn
  ``ApprovalSpec`` gate. Each entry has a ``<task_id>.pending`` JSON
  sentinel under ``.sdd/runtime/approvals/`` written by
  :func:`bernstein.core.orchestration.approval_gate.write_pending_sentinel`,
  carrying the operator-facing prompt and TTL deadline.
* **Spawn-pending** (legacy): tasks parked after janitor verification
  awaiting a merge approval. Stored under
  ``.sdd/runtime/pending_approvals/<task_id>.json`` by
  :class:`bernstein.core.security.approval.ApprovalGate`.

The two surfaces share a CLI verb because operators routinely run
``bernstein pending`` to see "what is currently waiting on me"; merging
them in a single command keeps that mental model intact while clearly
labelling the source. Use ``bernstein pending --kind approval`` /
``--kind spawn`` to filter when scripting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import click
from rich.table import Table

from bernstein.cli.helpers import console, is_json, print_json
from bernstein.core.orchestration.approval_gate import list_pending_approvals


def _load_spawn_pending(workdir: Path) -> list[dict[str, Any]]:
    """Return every entry in the legacy post-completion review queue."""
    pending_dir = workdir / ".sdd" / "runtime" / "pending_approvals"
    if not pending_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for entry in sorted(pending_dir.glob("*.json")):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rows.append({"task_id": entry.stem, "error": "unreadable"})
            continue
        if not isinstance(data, dict):
            data = {"task_id": entry.stem, "error": "not a JSON object"}
        rows.append(data)
    return rows


def _render_approval_table(rows: list[dict[str, Any]]) -> Table:
    """Render the pre-spawn approval queue as a Rich table."""
    table = Table(title="Approval-pending tasks (#1110)", show_header=True, header_style="bold magenta")
    table.add_column("Task ID", style="cyan")
    table.add_column("Prompt")
    table.add_column("Default Action", style="yellow")
    table.add_column("Times out at")
    for row in rows:
        table.add_row(
            str(row.get("task_id", "?")),
            str(row.get("prompt", ""))[:80],
            str(row.get("default_action", "reject")),
            str(row.get("timeout_at_iso", "")),
        )
    return table


def _render_spawn_table(rows: list[dict[str, Any]]) -> Table:
    """Render the legacy review queue as a Rich table."""
    table = Table(title="Spawn-pending tasks (post-completion review)", show_header=True, header_style="bold magenta")
    table.add_column("Task ID", style="cyan")
    table.add_column("Title")
    table.add_column("Tests")
    for row in rows:
        table.add_row(
            str(row.get("task_id", "?")),
            str(row.get("task_title", "")),
            str(row.get("test_summary", "")),
        )
    return table


@click.command("pending")
@click.option(
    "--workdir",
    default=".",
    help="Project root directory (parent of .sdd/).",
    type=click.Path(),
)
@click.option(
    "--kind",
    type=click.Choice(["all", "approval", "spawn"]),
    default="all",
    show_default=True,
    help="Filter the listed queue: 'approval' = pre-spawn gates (#1110); "
    "'spawn' = legacy post-completion review queue; 'all' shows both.",
)
def pending(workdir: str, kind: Literal["all", "approval", "spawn"]) -> None:
    """List tasks waiting for a human approval decision.

    Combines two file-backed queues:

    * Pre-spawn ``ApprovalSpec`` gates (``*.pending`` sentinels) — tasks
      that will not start until ``bernstein approve <id>`` writes the
      decision file.
    * Post-completion review gates — tasks parked after janitor
      verification awaiting merge.

    \b
    Examples:
      bernstein pending
      bernstein pending --kind approval
      bernstein pending --kind spawn
    """
    root = Path(workdir)
    approval_rows = list_pending_approvals(root) if kind in ("all", "approval") else []
    spawn_rows = _load_spawn_pending(root) if kind in ("all", "spawn") else []

    if is_json():
        print_json({"approval_pending": approval_rows, "spawn_pending": spawn_rows})
        return

    if not approval_rows and not spawn_rows:
        console.print("[dim]No tasks pending approval.[/dim]")
        return

    if approval_rows:
        console.print(_render_approval_table(approval_rows))
    if spawn_rows:
        console.print(_render_spawn_table(spawn_rows))

    console.print("\n[dim]Approve with:[/dim] bernstein approve <task_id>")
    console.print("[dim]Reject with:[/dim]  bernstein reject <task_id>")


__all__ = ["pending"]
