"""``bernstein run-lookup`` command: resolve a memorable run name (#1626).

User-facing surfaces render a deterministic ``<adjective>-<noun>-<NN>``
name from a run UUID (see :mod:`bernstein.cli.run_names`). This command
provides the reverse direction: given a memorable name and a set of known
run ids, print the UUID(s) that render to it.

Run ids are read from ``.sdd/runtime/run_id`` (the active run) plus any
ids supplied with ``--candidate``; this keeps the lookup self-contained
without reaching into a server endpoint.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

import click

from bernstein.cli.helpers import console
from bernstein.cli.run_names import find_collisions, is_run_name, render_name

__all__ = ["run_lookup_cmd"]


def _read_active_run_id(workspace_root: Path) -> UUID | None:
    """Return the active run id from ``.sdd/runtime/run_id`` if present."""
    run_id_path = workspace_root / ".sdd" / "runtime" / "run_id"
    if not run_id_path.is_file():
        return None
    raw = run_id_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _collect_candidates(workspace_root: Path, extra: tuple[str, ...]) -> list[UUID]:
    """Gather candidate run ids from the workspace and CLI flags."""
    candidates: list[UUID] = []
    active = _read_active_run_id(workspace_root)
    if active is not None:
        candidates.append(active)
    for value in extra:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise click.BadParameter(f"not a valid UUID: {value}") from exc
        candidates.append(parsed)
    return candidates


@click.command("run-lookup")
@click.argument("name")
@click.option(
    "--workspace-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Workspace root holding .sdd/runtime/run_id. Defaults to cwd.",
)
@click.option(
    "--candidate",
    "candidates",
    multiple=True,
    metavar="UUID",
    help="Extra run UUID to consider. May be repeated.",
)
def run_lookup_cmd(name: str, workspace_root: Path | None, candidates: tuple[str, ...]) -> None:
    """Resolve a memorable run NAME back to its run UUID.

    Exits non-zero when NAME is malformed or no known run id renders to it.
    """
    if not is_run_name(name):
        console.print(f"[red]'{name}' is not a valid run name (expected <adjective>-<noun>-NN).[/red]")
        sys.exit(2)

    root = workspace_root or Path.cwd()
    known = _collect_candidates(root, candidates)

    matches = [run_id for run_id in known if render_name(run_id) == name]
    if not matches:
        console.print(f"[yellow]No known run id renders to '{name}'.[/yellow]")
        console.print("[dim]Pass run UUIDs with --candidate to widen the search.[/dim]")
        sys.exit(1)

    collisions = find_collisions(known)
    if name in collisions:
        console.print(f"[yellow]Warning: '{name}' is shared by {len(matches)} run ids.[/yellow]")
    for run_id in matches:
        console.print(str(run_id))
