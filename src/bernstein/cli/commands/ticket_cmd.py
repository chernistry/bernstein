"""`bernstein from-ticket <url>` and `bernstein ticket ...` CLI commands.

Imports a task into Bernstein from a supported ticket URL (Linear web URL or
``linear://`` shortcut, GitHub issue URL, or Jira Cloud ``/browse/KEY-N`` URL).

The heavy lifting for the importer lives in
:mod:`bernstein.core.integrations.tickets`; the ``bernstein ticket validate``
sub-command delegates to :mod:`bernstein.sdd.validator`.
"""

from __future__ import annotations

import glob as _glob
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from bernstein.cli.helpers import console, is_json, print_json, server_post
from bernstein.core.integrations.tickets import (
    TicketAuthError,
    TicketParseError,
    TicketPayload,
    fetch_ticket,
)
from bernstein.sdd.validator import (
    SchemaNotFoundError,
    ValidationReport,
    validate_ticket,
)

__all__ = ["from_ticket", "ticket_group", "ticket_validate"]


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
            console.print("[yellow]Dry run: payload that would be sent:[/yellow]")
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


# ---------------------------------------------------------------------------
# bernstein ticket validate
# ---------------------------------------------------------------------------


def _expand_targets(patterns: tuple[str, ...]) -> list[Path]:
    """Expand a sequence of file paths / globs into a deterministic list."""
    seen: dict[Path, None] = {}
    for pat in patterns:
        if not pat:
            continue
        candidate = Path(pat)
        if candidate.exists():
            seen.setdefault(candidate.resolve(), None)
            continue
        matches = sorted(_glob.glob(pat, recursive=True))
        if not matches:
            # Still record the literal path so the report shows a missing-file
            # error instead of silently swallowing the input.
            seen.setdefault(candidate.resolve(), None)
            continue
        for m in matches:
            seen.setdefault(Path(m).resolve(), None)
    return list(seen.keys())


def _render_human(report: ValidationReport) -> str:
    if report.errors:
        tag = "[red][FAIL][/red]"
    elif report.warnings:
        tag = "[yellow][WARN][/yellow]"
    else:
        tag = "[green][OK]  [/green]"
    lines = [f"{tag} {report.path}"]
    for err in report.errors:
        lines.append(f"        - {err.render()}")
    for warn in report.warnings:
        lines.append(f"        - warning: {warn.render()}")
    return "\n".join(lines)


@ticket_group.command("validate")
@click.argument("paths", nargs=-1, required=True)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Promote recommended-key warnings to errors.",
)
@click.option(
    "--schema",
    "schema_version",
    default="v1",
    show_default=True,
    help="Schema version label (e.g. v1).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    show_default=True,
    help="Output format.",
)
def ticket_validate(
    paths: tuple[str, ...],
    strict: bool,
    schema_version: str,
    output_format: str,
) -> None:
    """Validate one or more SDD ticket files against the packaged JSON schema.

    PATHS may be one or more file paths or glob patterns. Exit codes:

    \b
      0  all files pass
      1  at least one file failed
      2  schema version not found
    """
    targets = _expand_targets(paths)
    if not targets:
        if output_format == "json":
            print_json({"status": "fail", "error": "no input paths"})
        else:
            console.print("[red]No input paths.[/red]")
        raise SystemExit(1)

    try:
        reports = [validate_ticket(p, schema_version=schema_version, strict=strict) for p in targets]
    except SchemaNotFoundError as exc:
        if output_format == "json":
            print_json({"status": "schema_not_found", "error": str(exc)})
        else:
            console.print(f"[red]Schema not found:[/red] {exc}")
        raise SystemExit(2) from exc

    any_failed = any(not r.ok for r in reports)

    if output_format == "json":
        payload = {
            "schema": schema_version,
            "strict": strict,
            "reports": [r.to_dict() for r in reports],
            "summary": {
                "total": len(reports),
                "ok": sum(1 for r in reports if r.status == "ok"),
                "warn": sum(1 for r in reports if r.status == "warn"),
                "fail": sum(1 for r in reports if r.status == "fail"),
            },
        }
        print_json(payload)
    else:
        for rep in reports:
            console.print(_render_human(rep))

    raise SystemExit(1 if any_failed else 0)
