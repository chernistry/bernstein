"""Bernstein memory: manage persistent agent memories."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import click
from rich.console import Console
from rich.table import Table

from bernstein.core.memory.cross_task_kb import CrossTaskKB, Scope, redact_value
from bernstein.core.memory.sqlite_store import MemoryType, SQLiteMemoryStore

_MEMORY_DB_PATH = ".sdd/memory/memory.db"

console = Console()


def _resolve_db_path() -> Path:
    """Return the SQLite path the CLI should operate on."""
    return Path(_MEMORY_DB_PATH)


def _resolve_run_id() -> str:
    """Return the active run id; falls back to ``BERNSTEIN_RUN_ID`` env var."""
    return os.environ.get("BERNSTEIN_RUN_ID", "")


def _resolve_task_id() -> str:
    """Return the active task id; falls back to ``BERNSTEIN_TASK_ID`` env var."""
    return os.environ.get("BERNSTEIN_TASK_ID", "manual-cli")


@click.group("memory")
def memory_group() -> None:
    """Manage persistent memories (conventions, decisions, learnings)."""
    pass


def _coerce_memory_type(value: str | None) -> MemoryType | None:
    """Convert a validated CLI value into the narrow ``MemoryType`` union."""
    return cast("MemoryType | None", value)


@memory_group.command("list")
@click.option(
    "--type",
    "memory_type",
    type=click.Choice(["convention", "decision", "learning"]),
    help="Filter by type.",
)
@click.option("--tag", multiple=True, help="Filter by tag.")
@click.option("--limit", default=20, help="Max entries to show.")
def list_memory(memory_type: str | None, tag: list[str], limit: int) -> None:
    """List stored memories."""
    db_path = Path(_MEMORY_DB_PATH)
    if not db_path.exists():
        console.print("[dim]No memory database found.[/dim]")
        return

    entries = SQLiteMemoryStore(db_path).list(type=_coerce_memory_type(memory_type), tags=tag or None, limit=limit)

    if not entries:
        console.print("[dim]No matching memories found.[/dim]")
        return

    table = Table(title="Persistent Memory")
    table.add_column("ID", style="dim")
    table.add_column("Type", style="cyan")
    table.add_column("Tags", style="green")
    table.add_column("Content")
    table.add_column("Age", style="dim")

    import datetime

    now = datetime.datetime.now()
    for e in entries:
        dt = datetime.datetime.fromtimestamp(e.created_at)
        age = str(now - dt).split(".")[0]
        table.add_row(
            str(e.id),
            e.type,
            ", ".join(e.tags),
            e.content[:100] + ("..." if len(e.content) > 100 else ""),
            f"{age} ago",
        )

    console.print(table)


@memory_group.command("add")
@click.argument("content")
@click.option(
    "--type",
    "memory_type",
    type=click.Choice(["convention", "decision", "learning"]),
    default="convention",
)
@click.option("--tag", multiple=True, help="Tags for this memory.")
def add_memory(content: str, memory_type: str, tag: list[str]) -> None:
    """Add a new persistent memory entry."""
    db_path = Path(_MEMORY_DB_PATH)
    entry_id = SQLiteMemoryStore(db_path).add(type=cast("MemoryType", memory_type), content=content, tags=tag.copy())
    console.print(f"[green]✓[/green] Added memory entry [bold]#{entry_id}[/bold]")


@memory_group.command("remove")
@click.argument("entry_id", type=int)
def remove_memory(entry_id: int) -> None:
    """Remove a memory entry by ID."""
    db_path = Path(_MEMORY_DB_PATH)
    if not db_path.exists():
        return
    store = SQLiteMemoryStore(db_path)
    if store.remove(entry_id):
        console.print(f"[green]✓[/green] Removed memory entry [bold]#{entry_id}[/bold]")
    else:
        console.print(f"[red]✗[/red] Entry [bold]#{entry_id}[/bold] not found")


# ---------------------------------------------------------------------------
# Cross-task knowledge share: publish/subscribe on tag-indexed memory.
# ---------------------------------------------------------------------------


@memory_group.command("share")
@click.argument("key")
@click.argument("value")
@click.option("--tag", required=True, help="Subscription tag for this fact.")
@click.option(
    "--scope",
    type=click.Choice(["run", "project"]),
    default="project",
    help="run = current orchestration run only; project = whole .sdd/ root.",
)
def share_fact(key: str, value: str, tag: str, scope: str) -> None:
    """Publish a fact under ``tag``/``key`` for other tasks to subscribe to.

    Manual operator escape hatch over the cross-task knowledge-base facade.
    """
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteMemoryStore(db_path)
    fact = (
        CrossTaskKB(
            store,
            run_id=_resolve_run_id(),
            producer_task_id=_resolve_task_id(),
        )
    ).publish(
        tag=tag,
        key=key,
        value=value,
        scope=cast("Scope", scope),
    )
    console.print(
        f"[green]✓[/green] Published [bold]{fact.tag}/{fact.key}[/bold] "
        f"(scope={fact.scope}, hash={fact.content_hash[:19]}...)"
    )


@memory_group.command("query")
@click.option("--tag", required=True, help="Subscription tag to query.")
@click.option(
    "--scope",
    type=click.Choice(["run", "project"]),
    default="project",
    help="run = current orchestration run only; project = whole .sdd/ root.",
)
@click.option(
    "--raw",
    is_flag=True,
    default=False,
    help="Print raw values without PII redaction. Off by default.",
)
def query_facts(tag: str, scope: str, raw: bool) -> None:
    """List facts published under ``tag`` with values redacted by default."""
    db_path = _resolve_db_path()
    if not db_path.exists():
        console.print("[dim]No memory database found.[/dim]")
        return

    store = SQLiteMemoryStore(db_path)
    facade = CrossTaskKB(
        store,
        run_id=_resolve_run_id(),
        producer_task_id=_resolve_task_id(),
    )
    facts = list(facade.subscribe(tag=tag, scope=cast("Scope", scope)))
    if not facts:
        console.print(f"[dim]No facts published under tag={tag!r} scope={scope!r}.[/dim]")
        return

    table = Table(title=f"Cross-task facts (tag={tag}, scope={scope})")
    table.add_column("Key", style="cyan")
    table.add_column("Producer", style="green")
    table.add_column("Value")
    table.add_column("Hash", style="dim")

    for fact in facts:
        display = fact.value if raw else redact_value(fact.value)
        if len(display) > 200:
            display = display[:200] + "..."
        table.add_row(
            fact.key,
            fact.producer_task_id or "(unknown)",
            display,
            fact.content_hash[:19] + "...",
        )

    console.print(table)
