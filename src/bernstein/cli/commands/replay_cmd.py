"""CLI helpers for the per-step replay surface (#1799).

The top-level ``bernstein replay`` command is implemented in
``advanced_cmd.py`` as a ``nargs=-1`` pseudo-group so the legacy
``bernstein replay <run_id>`` shape keeps working. This module hosts
the *new* verbs added by #1799:

* ``bernstein replay <agent_id>`` (interactive view + chain verification)
* ``bernstein replay export <agent_id>`` (portable, offline-verifiable receipt)
* ``bernstein replay publish <agent_id>`` (explicit opt-in, redacted receipt)
* ``bernstein replay verify <receipt_path>`` (offline verifier helper)

The dispatch functions are called from the existing ``replay`` command
in ``advanced_cmd.py``; keeping them in a separate module makes them
testable in isolation without standing up the whole CLI graph.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from bernstein.core.persistence.journal import (
    JournalReader,
    agent_journal_dir,
)
from bernstein.core.persistence.journal_diff import diff_journals
from bernstein.core.persistence.journal_export import (
    ReceiptError,
    export_receipt,
    verify_receipt,
)
from bernstein.core.persistence.journal_publish import (
    PublishError,
    RedactionPolicy,
    publish_receipt,
)

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_agent_dir(sdd_dir: Path, agent_id: str) -> Path:
    """Return the agent journal directory under *sdd_dir*."""
    return agent_journal_dir(sdd_dir, agent_id)


def replay_agent_view(
    agent_id: str,
    sdd_dir: Path,
    *,
    as_json: bool = False,
    limit: int | None = None,
) -> int:
    """Render the interactive per-step view for *agent_id*.

    Per AC #3 the view verifies the on-disk chain matches the recorded
    head *before* any rendering, so an operator never sees a tampered
    chain laid out as if it were intact.

    Returns the process exit code (0 on success, non-zero on chain
    verification failure).
    """
    from bernstein.cli.helpers import console

    agent_dir = _resolve_agent_dir(sdd_dir, agent_id)
    if not agent_dir.exists():
        console.print(f"[red]No journal for agent[/red] {agent_id} (looked at {agent_dir})")
        return 2

    reader = JournalReader(agent_dir)
    head_entry = reader.head()
    if head_entry is None:
        console.print(f"[yellow]Journal for {agent_id} is empty[/yellow]")
        return 0

    verification = reader.verify(expected_head=head_entry.step_hash)
    if not verification.ok:
        console.print(f"[red]Chain verification failed for {agent_id}:[/red]")
        for err in verification.errors:
            console.print(f"  - {err}")
        return 1

    entries = list(reader.entries())
    if limit is not None:
        entries = entries[:limit]

    if as_json:
        click_payload = {
            "agent_id": agent_id,
            "head_hash": verification.head_hash,
            "steps": verification.steps,
            "entries": [e.to_dict() for e in entries],
        }
        console.print_json(json.dumps(click_payload, default=str))
        return 0

    from rich.table import Table

    console.print(f"[bold]Replay for[/bold] [cyan]{agent_id}[/cyan]")
    console.print(f"[dim]head_hash:[/dim] [bold]{verification.head_hash}[/bold]")
    console.print(f"[dim]steps:[/dim] {verification.steps}")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("seq", style="dim", width=4)
    table.add_column("step_hash", width=18)
    table.add_column("prev_hash", width=18)
    table.add_column("model")
    table.add_column("tool_call")
    for entry in entries:
        tool_label = ""
        if entry.tool_call is not None:
            tool_label = str(entry.tool_call.get("name", "<tool>")) if isinstance(entry.tool_call, dict) else "<tool>"
        table.add_row(
            str(entry.seq),
            f"{entry.step_hash[:16]}...",
            f"{entry.prev_hash[:16]}...",
            entry.model or "-",
            tool_label,
        )
    console.print(table)
    console.print("[dim]Use 'bernstein replay export <agent_id>' to produce an offline-verifiable receipt.[/dim]")
    return 0


def replay_export(
    agent_id: str,
    sdd_dir: Path,
    output: Path,
    *,
    signer_key_path: Path | None = None,
) -> int:
    """Build a portable receipt and write it to *output*. Returns exit code."""
    from bernstein.cli.helpers import console

    agent_dir = _resolve_agent_dir(sdd_dir, agent_id)
    if not agent_dir.exists():
        console.print(f"[red]No journal for agent[/red] {agent_id}")
        return 2

    signer = None
    if signer_key_path is not None:
        from bernstein.core.persistence.lineage_signer import (
            Ed25519FileKeySigner,
            LineageSignerError,
        )

        try:
            signer = Ed25519FileKeySigner.from_path(signer_key_path)
        except LineageSignerError as exc:
            console.print(f"[red]Cannot load signing key:[/red] {exc}")
            return 2

    try:
        result = export_receipt(
            agent_dir,
            output,
            agent_id=agent_id,
            signer=signer,
        )
    except ReceiptError as exc:
        console.print(f"[red]Export failed:[/red] {exc}")
        return 1

    console.print(f"[green]Exported receipt[/green] -> {result.path}")
    console.print(f"  head_hash: {result.head_hash}")
    console.print(f"  steps:     {result.steps}")
    console.print(f"  signed:    {result.signed}")
    return 0


def replay_publish(
    agent_id: str,
    sdd_dir: Path,
    output: Path,
    *,
    opt_in: bool,
    signer_key_path: Path | None = None,
) -> int:
    """Publish a privacy-redacted receipt. Returns exit code."""
    from bernstein.cli.helpers import console

    if not opt_in:
        console.print(
            "[red]Refusing to publish:[/red] pass --yes-i-want-to-publish "
            "to confirm. Local-only is the default; publish is the only "
            "path that writes outside .sdd/runtime/."
        )
        return 2

    agent_dir = _resolve_agent_dir(sdd_dir, agent_id)
    if not agent_dir.exists():
        console.print(f"[red]No journal for agent[/red] {agent_id}")
        return 2

    signer = None
    if signer_key_path is not None:
        from bernstein.core.persistence.lineage_signer import (
            Ed25519FileKeySigner,
            LineageSignerError,
        )

        try:
            signer = Ed25519FileKeySigner.from_path(signer_key_path)
        except LineageSignerError as exc:
            console.print(f"[red]Cannot load signing key:[/red] {exc}")
            return 2

    try:
        result = publish_receipt(
            agent_dir,
            output,
            agent_id=agent_id,
            policy=RedactionPolicy.default(),
            opt_in=True,
            signer=signer,
        )
    except PublishError as exc:
        console.print(f"[red]Publish failed:[/red] {exc}")
        return 1

    console.print(f"[green]Published redacted receipt[/green] -> {result.path}")
    console.print(f"  original_head:  {result.original_head_hash}")
    console.print(f"  redacted_head:  {result.head_hash}")
    console.print(f"  steps:          {result.steps}")
    console.print(f"  signed:         {result.signed}")
    return 0


def replay_verify(
    receipt_path: Path,
    *,
    expected_head: str | None,
    public_key_path: Path | None,
) -> int:
    """Verify a receipt tarball offline. Returns exit code."""
    from bernstein.cli.helpers import console

    if not receipt_path.exists():
        console.print(f"[red]Receipt not found:[/red] {receipt_path}")
        return 2

    verifier = None
    if public_key_path is not None:
        from bernstein.core.persistence.lineage_signer import (
            Ed25519PublicKeyVerifier,
            LineageSignerError,
        )

        try:
            verifier = Ed25519PublicKeyVerifier.from_path(public_key_path)
        except LineageSignerError as exc:
            console.print(f"[red]Cannot load public key:[/red] {exc}")
            return 2

    try:
        result = verify_receipt(
            receipt_path,
            expected_head=expected_head,
            verifier=verifier,
        )
    except ReceiptError as exc:
        console.print(f"[red]Receipt malformed:[/red] {exc}")
        return 1

    if result.ok:
        console.print(f"[green]Receipt verified:[/green] head={result.head_hash} steps={result.steps}")
        return 0

    console.print(f"[red]Receipt failed verification ({len(result.errors)} error(s)):[/red]")
    for err in result.errors:
        console.print(f"  - {err}")
    return 1


def replay_diff_journals(
    left_agent_id: str,
    right_agent_id: str,
    sdd_dir: Path,
    *,
    as_json: bool = False,
) -> int:
    """Walk two agent journals side-by-side and surface the first divergence.

    Per AC #5 the orchestrator never silently accepts a divergent replay;
    this command is the operator-facing surface for that check.
    """
    from bernstein.cli.helpers import console

    left_dir = _resolve_agent_dir(sdd_dir, left_agent_id)
    right_dir = _resolve_agent_dir(sdd_dir, right_agent_id)
    if not left_dir.exists() or not right_dir.exists():
        console.print("[red]One or both journals are missing.[/red]")
        return 2

    divergence = diff_journals(left_dir, right_dir)
    if divergence is None:
        if as_json:
            console.print_json(json.dumps({"diverged": False}))
        else:
            console.print("[green]No divergence; chains match end-to-end.[/green]")
        return 0

    if as_json:
        payload = {
            "diverged": True,
            "seq": divergence.seq,
            "fields_changed": list(divergence.fields_changed),
            "left_values": divergence.left_values,
            "right_values": divergence.right_values,
            "reason": divergence.reason,
        }
        console.print_json(json.dumps(payload, default=str))
        return 1

    console.print(f"[yellow]Divergence at step {divergence.seq}[/yellow]: {divergence.reason}")
    for field in divergence.fields_changed:
        left = divergence.left_values.get(field)
        right = divergence.right_values.get(field)
        console.print(f"  [bold]{field}[/bold]:")
        console.print(f"    left:  {left!r}")
        console.print(f"    right: {right!r}")
    return 1


__all__ = [
    "replay_agent_view",
    "replay_diff_journals",
    "replay_export",
    "replay_publish",
    "replay_verify",
]
