"""CLI command group: ``bernstein consensus`` -- cross-cycle relay inspection.

Subcommands:

* ``show <cycle>``    -- pretty-print one cycle relay document.
* ``export <cycle>``  -- emit raw JSON, or markdown via ``--format md``.
* ``next``            -- print only the ``next_action`` of the head relay.
* ``list``            -- list every cycle id known to the chain.
* ``verify``          -- verify the HMAC chain on disk.

The relay store lives at ``.sdd/runtime/consensus/`` by default; override
with ``BERNSTEIN_ORCHESTRATION_RELAY_PATH`` or the global ``--path``
option.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from bernstein.core.orchestration.consensus_relay import (
    RelayChainError,
    RelayNotFoundError,
    RelayStore,
)


def _store(path: str | None) -> RelayStore:
    return RelayStore(Path(path) if path else None)


@click.group("consensus")
@click.option(
    "--path",
    "path_str",
    default=None,
    help="Override the relay directory (defaults to .sdd/runtime/consensus).",
)
@click.pass_context
def consensus_group(ctx: click.Context, path_str: str | None) -> None:
    """Inspect the cross-cycle consensus relay.

    \b
    Examples:
      bernstein consensus list
      bernstein consensus show cycle-42
      bernstein consensus next
      bernstein consensus export cycle-42 --format md
      bernstein consensus verify
    """
    ctx.ensure_object(dict)
    ctx.obj["path"] = path_str


@consensus_group.command("list")
@click.pass_context
def consensus_list(ctx: click.Context) -> None:
    """Print every cycle id, oldest first."""
    cycles = _store(ctx.obj.get("path")).cycles()
    if not cycles:
        click.echo("no relay entries")
        return
    for c in cycles:
        click.echo(c)


@consensus_group.command("show")
@click.argument("cycle_id", required=False, default=None)
@click.pass_context
def consensus_show(ctx: click.Context, cycle_id: str | None) -> None:
    """Pretty-print a relay document. Defaults to the current head."""
    store = _store(ctx.obj.get("path"))
    try:
        markdown = store.export_markdown(cycle_id)
    except RelayNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(markdown)


@consensus_group.command("export")
@click.argument("cycle_id", required=False, default=None)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "md"]),
    default="json",
    show_default=True,
    help="Output format.",
)
@click.pass_context
def consensus_export(ctx: click.Context, cycle_id: str | None, fmt: str) -> None:
    """Export a single cycle relay as JSON or markdown.

    With ``--format json`` the output is a single JSON object. With
    ``--format md`` the output is the same compact markdown produced by
    ``show``.
    """
    store = _store(ctx.obj.get("path"))
    try:
        if cycle_id is None:
            doc = store.head()
            if doc is None:
                raise click.ClickException("no relay entries")
        else:
            doc = store.read(cycle_id)
    except RelayNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    if fmt == "md":
        click.echo(store.export_markdown(doc.cycle_id))
    else:
        click.echo(json.dumps(doc.to_dict(), indent=2, sort_keys=True))


@consensus_group.command("next")
@click.pass_context
def consensus_next(ctx: click.Context) -> None:
    """Print the head relay's ``next_action`` -- the single follow-up to do.

    Used by ``bernstein run`` cold-start path. Exits with code 1 when no
    relay exists yet so shell scripts can branch.
    """
    head = _store(ctx.obj.get("path")).head()
    if head is None:
        raise click.ClickException("no relay entries")
    if not head.next_action:
        click.echo("")
        return
    click.echo(head.next_action)


@consensus_group.command("verify")
@click.pass_context
def consensus_verify(ctx: click.Context) -> None:
    """Verify the HMAC chain on disk.

    Exits with code 0 on a clean chain, 1 with an explanation otherwise.
    """
    store = _store(ctx.obj.get("path"))
    try:
        store.verify()
    except RelayChainError as exc:
        raise click.ClickException(f"chain invalid: {exc}") from exc
    click.echo("ok")
