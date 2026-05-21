"""``bernstein handoff`` CLI group - pass a session between surfaces (op-005).

A run started in the terminal can be continued from the web dashboard,
a chat bridge, or another terminal session - and vice versa. The
``handoff`` group is the operator-facing seam:

* ``bernstein handoff emit --session <id>`` freezes the source surface
  and prints a 5-minute resume token.
* ``bernstein handoff claim <token>`` re-attaches the current terminal
  to the same session, replaying the recent stream tail so the operator
  does not see a blank pane while the live stream catches up.

The same token works for the web dashboard (``GET /handoff/<token>``)
and the chat bridges (``/handoff <token>`` slash command); both routes
go through :mod:`bernstein.core.handoff` so the contract stays
single-sourced.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from bernstein.core.handoff import (
    HandoffClaimError,
    HandoffToken,
    HandoffUnknownTokenError,
    StreamTailBuffer,
    claim_token,
    emit_token,
)

if TYPE_CHECKING:
    from bernstein.core.handoff.tokens import Surface

__all__ = ["handoff_group"]

_DEFAULT_WORKDIR = Path.cwd
_SURFACE_CHOICES = ("terminal", "chat", "dashboard")
_DEFAULT_TAIL_LINES = 50


@click.group("handoff")
def handoff_group() -> None:
    """Move a live session between terminal, chat and dashboard."""


@handoff_group.command("emit")
@click.option(
    "--session",
    "session_id",
    required=True,
    help="Bernstein session id to hand off.",
)
@click.option(
    "--task",
    "task_id",
    default="",
    help="Active task id, if any.",
)
@click.option(
    "--from",
    "source_surface",
    type=click.Choice(_SURFACE_CHOICES),
    default="terminal",
    show_default=True,
    help="Surface that is freezing.",
)
@click.option(
    "--note",
    default="",
    help="Free-form note carried alongside the token (e.g. chat thread id).",
)
def handoff_emit(
    session_id: str,
    task_id: str,
    source_surface: str,
    note: str,
) -> None:
    """Freeze SESSION and print a short-lived resume token (5 min TTL)."""
    workdir = _DEFAULT_WORKDIR()
    surface = cast("Surface", source_surface)
    token = emit_token(
        workdir,
        session_id=session_id,
        task_id=task_id,
        source_surface=surface,
        note=note,
    )
    click.echo(token.token)
    click.echo(
        f"  session={token.session_id}"
        + (f" task={token.task_id}" if token.task_id else "")
        + f" expires_in={int(token.expires_at - token.issued_at)}s",
        err=True,
    )


@handoff_group.command("claim")
@click.argument("token", required=True)
@click.option(
    "--as",
    "claimed_by",
    type=click.Choice(_SURFACE_CHOICES),
    default="terminal",
    show_default=True,
    help="Destination surface claiming the token.",
)
@click.option(
    "--tail-lines",
    type=int,
    default=_DEFAULT_TAIL_LINES,
    show_default=True,
    help="Number of recent stream lines to replay.",
)
def handoff_claim(token: str, claimed_by: str, tail_lines: int) -> None:
    """Claim TOKEN, print session info, and replay the recent stream tail."""
    workdir = _DEFAULT_WORKDIR()
    surface = cast("Surface", claimed_by)
    try:
        record = claim_token(workdir, token, claimed_by=surface)
    except HandoffUnknownTokenError as exc:
        raise click.ClickException(f"unknown handoff token: {exc}") from exc
    except HandoffClaimError as exc:
        raise click.ClickException(f"could not claim handoff token: {exc}") from exc

    _render_attach_banner(record)
    _replay_tail(workdir, record.session_id, tail_lines)


@handoff_group.command("status")
def handoff_status() -> None:
    """List live (non-expired) handoff tokens."""
    from bernstein.core.handoff import HandoffTokenStore

    workdir = _DEFAULT_WORKDIR()
    tokens = HandoffTokenStore(workdir).all()
    if not tokens:
        click.echo("handoff: no live tokens.")
        return
    for tok in tokens:
        state = "claimed" if tok.claimed else "pending"
        click.echo(
            f"{tok.token[:12]}... session={tok.session_id} "
            f"task={tok.task_id or '-'} from={tok.source_surface} "
            f"state={state} ttl_left={int(tok.expires_at - tok.issued_at)}s"
        )


def _render_attach_banner(record: HandoffToken) -> None:
    click.echo(
        f"handoff: attached to session {record.session_id}"
        + (f" / task {record.task_id}" if record.task_id else "")
        + f" (from {record.source_surface})"
    )
    if record.note:
        click.echo(f"handoff: note={record.note}")


def _replay_tail(workdir: Path, session_id: str, tail_lines: int) -> None:
    if tail_lines <= 0:
        return
    entries = StreamTailBuffer(workdir, session_id).read(limit=tail_lines)
    if not entries:
        click.echo("handoff: no buffered tail to replay.")
        return
    click.echo(f"handoff: replaying last {len(entries)} line(s):")
    for entry in entries:
        click.echo(f"  [{entry.surface}] {entry.text}")
