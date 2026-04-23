"""`bernstein from-ticket <url>` CLI command.

Imports a task into Bernstein from a supported ticket URL (Linear web URL or
``linear://`` shortcut, GitHub issue URL, or Jira Cloud ``/browse/KEY-N`` URL).

The heavy lifting lives in :mod:`bernstein.core.integrations.tickets`; this
module only handles CLI plumbing, role/scope inference, and POSTing to the
task server.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import click

from bernstein.cli.helpers import console, is_json, print_json, server_post
from bernstein.core.integrations.tickets import (
    TicketAuthError,
    TicketParseError,
    TicketPayload,
    fetch_ticket,
)

__all__ = ["from_ticket", "ticket_group"]


# ---------------------------------------------------------------------------
# Label -> role / scope inference
# ---------------------------------------------------------------------------

_DEFAULT_ROLE = "backend"

# Priority order matters: the first match wins, so that (for example) a
# ``bug`` label routes to ``qa`` even if the ticket is also labelled ``frontend``.
_ROLE_LABEL_MAP: tuple[tuple[str, str], ...] = (
    ("bug", "qa"),
    ("qa", "qa"),
    ("test", "qa"),
    ("docs", "docs"),
    ("documentation", "docs"),
    ("security", "security"),
    ("devops", "devops"),
    ("infra", "devops"),
    ("ops", "devops"),
    ("frontend", "frontend"),
    ("ui", "frontend"),
    ("ux", "frontend"),
    ("design", "design"),
    ("backend", "backend"),
    ("api", "backend"),
    ("data", "data"),
)

_SCOPE_LABEL_MAP: tuple[tuple[str, str], ...] = (
    ("xs", "small"),
    ("s", "small"),
    ("small", "small"),
    ("epic", "large"),
    ("xl", "large"),
    ("l", "large"),
    ("large", "large"),
    ("m", "medium"),
    ("medium", "medium"),
)


def _normalize(labels: tuple[str, ...]) -> set[str]:
    return {lab.strip().lower() for lab in labels if lab}


def infer_role(labels: tuple[str, ...], default: str = _DEFAULT_ROLE) -> str:
    """Return a Bernstein role inferred from ticket labels, or *default*."""
    lowered = _normalize(labels)
    for needle, role in _ROLE_LABEL_MAP:
        if needle in lowered:
            return role
    return default


def infer_scope(labels: tuple[str, ...], default: str = "medium") -> str:
    """Return a task scope inferred from ticket labels, or *default*."""
    lowered = _normalize(labels)
    for needle, scope in _SCOPE_LABEL_MAP:
        if needle in lowered:
            return scope
    return default


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

_PRIORITY_MAP: dict[str, int] = {"low": 3, "medium": 2, "high": 1}


def _render_markdown(payload: TicketPayload) -> str:
    lines: list[str] = [f"# {payload.title}" if payload.title else f"# {payload.id}", ""]
    if payload.description:
        lines.append(payload.description.rstrip())
        lines.append("")
    if payload.labels:
        lines.append(f"**Labels:** {', '.join(payload.labels)}")
    if payload.assignee:
        lines.append(f"**Assignee:** {payload.assignee}")
    if payload.url:
        lines.append(f"**Source:** {payload.url}")
    return "\n".join(lines).rstrip() + "\n"


def build_task_payload(
    ticket: TicketPayload,
    *,
    role: str | None,
    priority: str | None,
) -> dict[str, Any]:
    """Build the JSON payload for ``POST /tasks`` from a ticket + CLI flags."""
    chosen_role = role if role else infer_role(ticket.labels)
    scope = infer_scope(ticket.labels)
    priority_int = _PRIORITY_MAP.get(priority or "medium", 2)
    description = _render_markdown(ticket)
    return {
        "title": ticket.title or ticket.id,
        "description": description,
        "role": chosen_role,
        "priority": priority_int,
        "scope": scope,
        "complexity": "medium",
        "depends_on": [],
        "metadata": {
            "source": ticket.source,
            "external_id": ticket.id,
            "url": ticket.url,
        },
    }


# ---------------------------------------------------------------------------
# Click command
# ---------------------------------------------------------------------------


def _print_payload(payload: dict[str, Any]) -> None:
    if is_json():
        print_json(payload)
    else:
        console.print_json(json.dumps(payload, indent=2))


def _do_import(
    url: str,
    *,
    role: str | None,
    priority: str | None,
    dry_run: bool,
    run: bool,
) -> None:
    try:
        ticket = fetch_ticket(url)
    except TicketAuthError as exc:
        console.print(f"[red]Authentication error:[/red] {exc}")
        raise SystemExit(2) from exc
    except TicketParseError as exc:
        console.print(f"[red]Could not parse ticket:[/red] {exc}")
        raise SystemExit(1) from exc

    payload = build_task_payload(ticket, role=role, priority=priority)

    if dry_run:
        if not is_json():
            console.print("[yellow]Dry run — payload that would be sent:[/yellow]")
        _print_payload(payload)
        return

    result = server_post("/tasks", payload)
    if result is None:
        from bernstein.cli.errors import server_unreachable

        server_unreachable().print()
        raise SystemExit(1)

    task_id = str(result.get("id", "?"))
    if is_json():
        print_json(result)
    else:
        console.print(f"[green]Imported[/green] [bold]{task_id}[/bold] from [cyan]{ticket.source}[/cyan] ({ticket.id})")

    if run:
        rc = subprocess.call([sys.executable, "-m", "bernstein", "run", "--task", task_id])
        if rc != 0:
            raise SystemExit(rc)


_COMMON_OPTIONS = [
    click.argument("url", metavar="URL"),
    click.option(
        "--role",
        default=None,
        help="Assign a specific role. Defaults to label-based inference, then 'backend'.",
    ),
    click.option(
        "--priority",
        type=click.Choice(["low", "medium", "high"]),
        default=None,
        help="Override the task priority.",
    ),
    click.option(
        "--dry-run",
        is_flag=True,
        default=False,
        help="Print the task payload without contacting the server.",
    ),
    click.option(
        "--run",
        is_flag=True,
        default=False,
        help="Invoke `bernstein run` on the task immediately after creating it.",
    ),
]


def _apply_common(func: Any) -> Any:
    for decorator in reversed(_COMMON_OPTIONS):
        func = decorator(func)
    return func


@click.command("from-ticket")
@_apply_common
def from_ticket(url: str, role: str | None, priority: str | None, dry_run: bool, run: bool) -> None:
    """Import a task into Bernstein from a ticket URL.

    URL may be any of:

    \b
      * https://linear.app/<workspace>/issue/<KEY-N>   or  linear://KEY-N
      * https://github.com/<owner>/<repo>/issues/<n>
      * https://<your-domain>/browse/<KEY-N>           (Jira Cloud)
    """
    _do_import(url, role=role, priority=priority, dry_run=dry_run, run=run)


@click.group("ticket")
def ticket_group() -> None:
    """Ticket import utilities."""


@ticket_group.command("import")
@_apply_common
def ticket_import(url: str, role: str | None, priority: str | None, dry_run: bool, run: bool) -> None:
    """Alias for `bernstein from-ticket`. Imports a task from a ticket URL."""
    _do_import(url, role=role, priority=priority, dry_run=dry_run, run=run)
