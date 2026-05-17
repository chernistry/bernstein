"""``bernstein telemetry`` subcommand.

Operator-facing surface:

    bernstein telemetry on        opt in, write config, generate install id
    bernstein telemetry off       opt out, delete install id
    bernstein telemetry status    show current state + which signal won
    bernstein telemetry export    dump last 30 days of locally queued events

The output is deliberately compact and deterministic to enable snapshot
tests against the ``status`` command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from bernstein.core.telemetry import (
    OptInSource,
    config_file_path,
    ensure_install_id,
    install_id_path,
    queue_path,
    read_install_id,
    read_recent_events,
    reset_default_client,
    reset_install_id,
    resolve,
    write_enabled,
)


@click.group("telemetry")
def telemetry_group() -> None:
    """Manage opt-in operator observability.

    Bernstein collects no telemetry by default.  These commands let an
    operator opt in to a strictly bounded event set, inspect the local
    queue, and opt back out at any time.
    """


@telemetry_group.command("on")
@click.option(
    "--home",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the operator home directory (testing).",
)
def telemetry_on(home: Path | None) -> None:
    """Opt in.  Writes ``enabled: true`` and generates an install id."""
    write_enabled(True, home=home)
    reset_default_client()
    install_id = ensure_install_id(home=home)
    click.echo(f"telemetry: enabled (install_id={install_id[:8]}...)")


@telemetry_group.command("off")
@click.option(
    "--home",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the operator home directory (testing).",
)
def telemetry_off(home: Path | None) -> None:
    """Opt out.  Writes ``enabled: false`` and deletes the install id."""
    write_enabled(False, home=home)
    reset_install_id(home=home)
    reset_default_client()
    click.echo("telemetry: disabled")


@telemetry_group.command("status")
@click.option(
    "--home",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the operator home directory (testing).",
)
def telemetry_status(home: Path | None) -> None:
    """Show current state and which precedence layer determined it."""
    state = resolve(home=home)
    install_id = read_install_id(home=home)
    lines: list[str] = []
    lines.append(f"enabled: {str(state.enabled).lower()}")
    lines.append(f"source: {state.source.value}")
    lines.append(f"install_id: {install_id or 'none'}")
    lines.append(f"config_file: {config_file_path(home)}")
    lines.append(f"install_id_path: {install_id_path(home)}")
    lines.append(f"queue: {queue_path(home)}")
    click.echo("\n".join(lines))


@telemetry_group.command("export")
@click.option(
    "--days",
    type=int,
    default=30,
    show_default=True,
    help="Number of days of locally queued events to dump.",
)
@click.option(
    "--home",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the operator home directory (testing).",
)
def telemetry_export(days: int, home: Path | None) -> None:
    """Dump locally queued events as JSONL."""
    for line in read_recent_events(days=days, home=home):
        click.echo(line)


def explain_source(source: OptInSource) -> str:
    """Return a one-line operator-facing description of ``source``."""
    if source is OptInSource.DO_NOT_TRACK:
        return "DO_NOT_TRACK env var (universal opt-out)"
    if source is OptInSource.ENV:
        return "BERNSTEIN_TELEMETRY env var"
    if source is OptInSource.FILE:
        return "config file (~/.bernstein/telemetry.yaml)"
    return "default (off)"


# Expose the group for registration in main.py.
def register(parent: Any) -> None:
    """Register this subgroup on a parent click group."""
    parent.add_command(telemetry_group)


__all__ = [
    "explain_source",
    "register",
    "telemetry_export",
    "telemetry_group",
    "telemetry_off",
    "telemetry_on",
    "telemetry_status",
]
