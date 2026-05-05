"""``bernstein lineage verify <run_id>`` -- one-shot chain verification.

Walks every lineage record for a run, re-checks the WAL hash chain,
and (when a customer public key is supplied) re-verifies every
``customer_signature``. Exits 0 only when the entire run validates.

Designed for compliance-team ad-hoc use: an auditor runs this against
a sealed evidence package to confirm nothing has been edited between
artefact handover and review. The exit code makes it pipeline-safe --
the operator can wire this into a periodic CI job.
"""

from __future__ import annotations

from pathlib import Path

import click

from bernstein.cli.helpers import console


@click.command(name="verify")
@click.argument("run_id", required=True)
@click.option(
    "--workdir",
    "-w",
    type=click.Path(file_okay=False, exists=True),
    default=".",
    show_default=True,
    help="Project root containing .sdd/.",
)
@click.option(
    "--public-key",
    "public_key_path",
    type=click.Path(dir_okay=False, exists=True),
    default=None,
    help="Customer Ed25519 public key (PEM or raw 32-byte). When supplied, every customer_signature is re-verified.",
)
def lineage_verify_cmd(run_id: str, workdir: str, public_key_path: str | None) -> None:
    """Verify the lineage chain for *run_id*. Exits 0 on success, non-zero on tamper."""
    from bernstein.core.persistence.lineage import verify_run_chain
    from bernstein.core.persistence.lineage_signer import (
        Ed25519PublicKeyVerifier,
        LineageSignerError,
    )

    sdd_dir = Path(workdir).resolve() / ".sdd"
    if not sdd_dir.is_dir():
        console.print(f"[red]No .sdd directory at[/red] {sdd_dir}")
        raise SystemExit(1)

    verifier = None
    if public_key_path is not None:
        try:
            verifier = Ed25519PublicKeyVerifier.from_path(Path(public_key_path))
        except LineageSignerError as exc:
            console.print(f"[red]Bad public key:[/red] {exc}")
            raise SystemExit(1) from exc

    result = verify_run_chain(sdd_dir, run_id, verifier=verifier)

    console.print()
    console.print(f"[bold]Lineage verification[/bold] run={run_id} records={result.record_count}")
    if result.ok:
        console.print("[green]OK[/green] -- chain intact, all signatures valid.")
        raise SystemExit(0)

    console.print(f"[red]TAMPER DETECTED[/red] -- {len(result.errors)} error(s):")
    for err in result.errors[:50]:
        console.print(f"  - {err}")
    if len(result.errors) > 50:
        console.print(f"  ... ({len(result.errors) - 50} more)")
    raise SystemExit(2)
