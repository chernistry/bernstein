"""Audit CLI — Merkle-tree integrity seal and verification.

Commands:
  bernstein audit seal             Compute and store a Merkle root.
  bernstein audit seal --anchor-git  Also create a git tag.
  bernstein audit verify --merkle  Verify the Merkle tree against disk.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from bernstein.cli.helpers import console

AUDIT_DIR = Path(".sdd/audit")
MERKLE_DIR = AUDIT_DIR / "merkle"


@click.group("audit")
def audit_group() -> None:
    """Audit log integrity tools."""


@audit_group.command("seal")
@click.option("--anchor-git", is_flag=True, default=False, help="Anchor root hash as a git tag.")
def seal_cmd(anchor_git: bool) -> None:
    """Compute a Merkle root across all audit log files and store the seal."""
    from bernstein.core.merkle import anchor_to_git, compute_seal, save_seal

    if not AUDIT_DIR.is_dir():
        console.print(f"[red]Audit directory not found:[/red] {AUDIT_DIR}")
        console.print("[dim]Ensure the audit log is active (bernstein must have written audit events).[/dim]")
        raise SystemExit(1)

    try:
        _tree, seal = compute_seal(AUDIT_DIR)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None

    seal_path = save_seal(seal, MERKLE_DIR)

    # Display result
    console.print()
    console.print(
        Panel(
            "[bold]Merkle Audit Seal[/bold]",
            border_style="green",
            expand=False,
        )
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=14)
    table.add_column("Value")
    table.add_row("Root hash", str(seal["root_hash"]))
    table.add_row("Leaves", str(seal["leaf_count"]))
    table.add_row("Algorithm", str(seal["algorithm"]))
    table.add_row("Sealed at", str(seal["sealed_at_iso"]))
    table.add_row("Seal file", str(seal_path))
    console.print(table)

    if anchor_git:
        root_hash = str(seal["root_hash"])
        tag = anchor_to_git(root_hash, Path.cwd())
        if tag:
            console.print(f"\n  [green]Git tag created:[/green] {tag}")
        else:
            console.print("\n  [yellow]Git anchoring failed (not a git repo or tag exists).[/yellow]")

    console.print()


@audit_group.command("verify")
@click.option("--merkle", is_flag=True, default=False, help="Verify Merkle tree integrity across log files.")
def verify_cmd(merkle: bool) -> None:
    """Verify audit log integrity.

    \b
      bernstein audit verify --merkle   Validate the Merkle tree
    """
    if not merkle:
        console.print("[dim]Use --merkle to verify Merkle tree integrity.[/dim]")
        console.print("[dim]HMAC chain verification coming with the base audit module.[/dim]")
        return

    from bernstein.core.merkle import verify_merkle

    result = verify_merkle(AUDIT_DIR, MERKLE_DIR)

    console.print()
    if result.valid:
        console.print(
            Panel(
                "[bold green]Merkle Verification Passed[/bold green]",
                border_style="green",
                expand=False,
            )
        )
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="dim", no_wrap=True, min_width=14)
        table.add_column("Value")
        table.add_row("Root hash", result.root_hash)
        if result.seal_path:
            table.add_row("Seal file", str(result.seal_path))
        console.print(table)
    else:
        console.print(
            Panel(
                "[bold red]Merkle Verification FAILED[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        for err in result.errors:
            console.print(f"  [red]![/red] {err}")

    console.print()
    raise SystemExit(0 if result.valid else 1)
