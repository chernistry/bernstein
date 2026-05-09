"""Audit CLI — Merkle-tree integrity seal and verification.

Commands:
  bernstein audit show             Show recent audit log events.
  bernstein audit seal             Compute and store a Merkle root.
  bernstein audit seal --anchor-git  Also create a git tag.
  bernstein audit verify --merkle  Verify the Merkle tree against disk.
"""

from __future__ import annotations

import contextlib
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


@audit_group.command("show")
@click.option("--limit", default=20, show_default=True, help="Maximum number of events to show.")
def show_cmd(limit: int) -> None:
    """Show recent audit log events from .sdd/audit/."""
    import json as _json

    if not AUDIT_DIR.is_dir():
        console.print(
            "[yellow]No audit log found.[/yellow]  Run [bold]bernstein run[/bold] first to generate audit events."
        )
        return

    log_files = sorted(AUDIT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        console.print(
            "[yellow]Audit directory exists but contains no log files.[/yellow]  "
            "Run [bold]bernstein run[/bold] to generate audit events."
        )
        return

    events: list[dict] = []
    for lf in log_files:
        try:
            for line in lf.read_text().splitlines():
                line = line.strip()
                if line:
                    with contextlib.suppress(_json.JSONDecodeError):
                        events.append(_json.loads(line))
        except OSError:
            pass
        if len(events) >= limit:
            break

    events = events[:limit]

    table = Table(show_header=True, header_style="bold magenta", show_lines=False)
    table.add_column("Timestamp", style="dim", no_wrap=True)
    table.add_column("Event", style="bold")
    table.add_column("Actor")
    table.add_column("Resource")

    for ev in events:
        ts = str(ev.get("timestamp", "—"))[:19]
        event_type = str(ev.get("event_type", "—"))
        actor = str(ev.get("actor", ""))
        resource = f"{ev.get('resource_type', '')}/{ev.get('resource_id', '')}"
        table.add_row(ts, event_type, actor, resource)

    console.print()
    console.print(table)
    console.print(f"\n[dim]Showing {len(events)} event(s) from {AUDIT_DIR}[/dim]\n")


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
@click.option("--merkle-only", is_flag=True, default=False, help="Only verify Merkle tree (skip HMAC chain).")
@click.option("--hmac-only", is_flag=True, default=False, help="Only verify HMAC chain (skip Merkle tree).")
def verify_cmd(merkle_only: bool, hmac_only: bool) -> None:
    """Verify audit log integrity (HMAC chain + Merkle tree).

    \b
      bernstein audit verify              Verify both HMAC chain and Merkle tree
      bernstein audit verify --hmac-only  Verify HMAC chain only
      bernstein audit verify --merkle-only  Verify Merkle tree only
    """
    if not AUDIT_DIR.is_dir():
        console.print(f"[red]Audit directory not found:[/red] {AUDIT_DIR}")
        raise SystemExit(1)

    all_passed = True

    if not merkle_only:
        all_passed = _verify_hmac_chain() and all_passed

    if not hmac_only:
        all_passed = _verify_merkle_tree() and all_passed

    console.print()
    raise SystemExit(0 if all_passed else 1)


def _verify_hmac_chain() -> bool:
    """Verify HMAC chain and print results. Returns True if valid."""
    from bernstein.core.audit import AuditLog

    audit_log = AuditLog(AUDIT_DIR)
    hmac_valid, hmac_errors = audit_log.verify()

    console.print()
    if hmac_valid:
        console.print(
            Panel("[bold green]HMAC Chain Verification Passed[/bold green]", border_style="green", expand=False)
        )
        return True
    console.print(Panel("[bold red]HMAC Chain Verification FAILED[/bold red]", border_style="red", expand=False))
    for err in hmac_errors:
        console.print(f"  [red]![/red] {err}")
    return False


def _verify_merkle_tree() -> bool:
    """Verify Merkle tree and print results. Returns True if valid."""
    from bernstein.core.merkle import verify_merkle

    result = verify_merkle(AUDIT_DIR, MERKLE_DIR)

    console.print()
    if result.valid:
        console.print(Panel("[bold green]Merkle Verification Passed[/bold green]", border_style="green", expand=False))
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="dim", no_wrap=True, min_width=14)
        table.add_column("Value")
        table.add_row("Root hash", result.root_hash)
        if result.seal_path:
            table.add_row("Seal file", str(result.seal_path))
        console.print(table)
        return True
    console.print(Panel("[bold red]Merkle Verification FAILED[/bold red]", border_style="red", expand=False))
    for err in result.errors:
        console.print(f"  [red]![/red] {err}")
    return False


@audit_group.command("verify-hmac")
def verify_hmac_cmd() -> None:
    """Verify HMAC chain integrity across all audit log files."""
    from bernstein.core.audit import AuditLog

    if not AUDIT_DIR.is_dir():
        console.print(f"[red]Audit directory not found:[/red] {AUDIT_DIR}")
        raise SystemExit(1)

    audit_log = AuditLog(AUDIT_DIR)
    valid, errors = audit_log.verify()

    console.print()
    if valid:
        console.print(
            Panel(
                "[bold green]HMAC Chain Verification Passed[/bold green]",
                border_style="green",
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]HMAC Chain Verification FAILED[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        for err in errors:
            console.print(f"  [red]![/red] {err}")

    console.print()
    raise SystemExit(0 if valid else 1)


@audit_group.command("export")
@click.option(
    "--period",
    default=None,
    help="SOC 2 time period to export (e.g. Q1-2026, 2026-03, 2026).",
)
@click.option(
    "--article-12",
    "article_12",
    is_flag=True,
    default=False,
    help="Emit an EU AI Act Article 12 evidence pack (uses --since/--until).",
)
@click.option(
    "--since",
    default=None,
    help="ISO-8601 inclusive lower bound (Article 12 mode).",
)
@click.option(
    "--until",
    default=None,
    help="ISO-8601 exclusive upper bound (Article 12 mode).",
)
@click.option(
    "--risk-class",
    "risk_class",
    default="limited",
    type=click.Choice(["high", "limited", "minimal"]),
    show_default=True,
    help="EU AI Act risk class driving Article 12(3) retention horizon.",
)
@click.option(
    "--format",
    "fmt",
    default="zip",
    type=click.Choice(["zip", "dir"]),
    show_default=True,
    help="Output format (SOC 2 mode only; Article 12 always emits a zip).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output directory (defaults to .sdd/evidence/).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Article 12 mode: build the bundle in-memory and print the manifest "
    "without writing to disk.",
)
@click.option("--dir", "workdir", default=".", show_default=True, help="Project root directory.")
def export_cmd(  # noqa: PLR0913 — CLI surface mirrors documented flags
    period: str | None,
    article_12: bool,
    since: str | None,
    until: str | None,
    risk_class: str,
    fmt: str,
    output: str | None,
    dry_run: bool,
    workdir: str,
) -> None:
    """Export an evidence package for auditors.

    \b
    Two modes:
      * SOC 2 mode (default): bernstein audit export --period Q1-2026
      * EU AI Act Article 12: bernstein audit export --article-12 \
            --since 2026-08-01T00:00:00+00:00 --until 2026-09-01T00:00:00+00:00

    \b
    SOC 2 mode collects audit logs, HMAC verification, Merkle seals,
    compliance config, WAL entries, and SBOM into a single package.

    \b
    Article 12 mode emits a deterministic, retention-pinned bundle with
    the audit log slice, a data-governance catalog, and an EU-AI-Act
    clause map (manifest.json contains artefact SHA-256 hashes for
    auditor verification).
    """
    sdd_dir = Path(workdir).resolve() / ".sdd"
    if not sdd_dir.is_dir():
        console.print(f"[red]State directory not found:[/red] {sdd_dir}")
        console.print("[dim]Run [bold]bernstein run[/bold] first to generate audit data.[/dim]")
        raise SystemExit(1)

    if article_12:
        _run_article12_export(
            sdd_dir=sdd_dir,
            since=since,
            until=until,
            risk_class=risk_class,
            output=output,
            dry_run=dry_run,
        )
        return

    if not period:
        console.print(
            "[red]Either --period (SOC 2) or --article-12 (with --since/--until) is required.[/red]"
        )
        raise SystemExit(2)

    from bernstein.core.compliance import export_soc2_package, parse_period

    # Validate period before doing work
    try:
        start, end = parse_period(period)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None

    output_path = Path(output).resolve() if output else None

    try:
        result = export_soc2_package(sdd_dir, period, output_path=output_path, fmt=fmt)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None

    # Display summary
    console.print()
    console.print(
        Panel(
            "[bold]SOC 2 Evidence Package[/bold]",
            border_style="green",
            expand=False,
        )
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=14)
    table.add_column("Value")
    table.add_row("Period", f"{period}  ({start} to {end})")
    table.add_row("Format", fmt)
    table.add_row("Output", str(result))
    console.print(table)
    console.print()


def _run_article12_export(
    *,
    sdd_dir: Path,
    since: str | None,
    until: str | None,
    risk_class: str,
    output: str | None,
    dry_run: bool,
) -> None:
    """Execute the EU AI Act Article 12 evidence-pack flow."""
    import json as _json
    from typing import cast

    from bernstein.core.security.article12_bundle import (
        Article12Bundle,
        RiskClass,
        build_article12_bundle,
    )

    if not since or not until:
        console.print("[red]--article-12 requires both --since and --until (ISO-8601).[/red]")
        raise SystemExit(2)

    audit_dir = sdd_dir / "audit"
    output_dir = Path(output).resolve() if output else None

    try:
        bundle: Article12Bundle = build_article12_bundle(
            audit_dir=audit_dir,
            since=since,
            until=until,
            risk_class=cast("RiskClass", risk_class),
            output_dir=output_dir,
            write=not dry_run,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None

    console.print()
    console.print(
        Panel(
            "[bold]EU AI Act Article 12 Evidence Pack[/bold]",
            border_style="green",
            expand=False,
        )
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=18)
    table.add_column("Value")
    table.add_row("Bundle ID", bundle.bundle_id)
    table.add_row("Window", f"{bundle.since} → {bundle.until}")
    table.add_row("Risk class", bundle.risk_class)
    table.add_row("Events", str(bundle.event_count))
    table.add_row("Chain anchor", bundle.chain_anchor[:16] + "…")
    table.add_row("Retention until", bundle.retention.retention_until)
    table.add_row("SHA-256", bundle.sha256[:16] + "…")
    if bundle.archive_path is not None:
        table.add_row("Archive", str(bundle.archive_path))
    elif dry_run:
        table.add_row("Archive", "(dry-run, not written)")
    console.print(table)
    console.print()

    if dry_run:
        console.print("[dim]Manifest (dry-run):[/dim]")
        console.print(_json.dumps(bundle.to_dict(), indent=2))
        console.print()


@audit_group.command("capabilities")
@click.option(
    "--workdir",
    default=".",
    show_default=True,
    help="Project root (used to load templates/capabilities/).",
)
def capabilities_cmd(workdir: str) -> None:
    """Print the lethal-trifecta capability matrix and any violations.

    Loads tool capability declarations from
    ``<workdir>/templates/capabilities/`` (falling back to the bundled
    defaults), prints the matrix, and scans recorded spawn manifests
    under ``.sdd/runtime/spawn_capabilities/`` for any chain that trips
    all three capabilities.  Exits non-zero when a violation is found.
    """
    import json as _json

    from bernstein.core.security.capability_matrix import (
        Capability,
        CapabilityRegistry,
        EnforcementMode,
        find_violating_chains,
    )

    root = Path(workdir).resolve()
    registry = CapabilityRegistry.load_default(workdir=root, mode=EnforcementMode.ENFORCE)

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Tool", style="bold")
    table.add_column("Source", style="dim")
    for cap in Capability:
        table.add_column(cap.value, justify="center")

    for name in sorted(registry.tools):
        entry = registry.tools[name]
        row: list[str] = [name, entry.source]
        for cap in Capability:
            row.append("[green]Y[/green]" if cap in entry.capabilities else "[dim]-[/dim]")
        table.add_row(*row)

    console.print()
    console.print(Panel("[bold]Tool Capability Matrix[/bold]", border_style="cyan", expand=False))
    console.print(table)
    console.print(f"\n[dim]{len(registry.tools)} tool(s) declared[/dim]\n")

    runtime_dir = root / ".sdd" / "runtime" / "spawn_capabilities"
    chains: list[list[str]] = []
    if runtime_dir.is_dir():
        for path in sorted(runtime_dir.glob("*.json")):
            try:
                manifest = _json.loads(path.read_text(encoding="utf-8"))
            except (OSError, _json.JSONDecodeError):
                continue
            tools = manifest.get("tools", [])
            if isinstance(tools, list):
                chains.append([str(t) for t in tools])

    violations = find_violating_chains(registry, chains)
    if not violations:
        console.print("[green]No lethal-trifecta violations in recorded spawns.[/green]\n")
        return

    console.print(
        Panel(
            f"[bold red]{len(violations)} lethal-trifecta violation(s)[/bold red]",
            border_style="red",
            expand=False,
        )
    )
    for decision in violations:
        console.print(f"  [red]![/red] {decision.reason} — tools=[bold]{list(decision.offending_tools)}[/bold]")
    console.print()
    raise SystemExit(1)


@audit_group.command("query")
@click.option("--event-type", default=None, help="Filter by event type.")
@click.option("--actor", default=None, help="Filter by actor.")
@click.option("--since", default=None, help="ISO 8601 lower bound (inclusive).")
@click.option("--limit", default=50, show_default=True, help="Maximum number of events to return.")
def query_cmd(event_type: str | None, actor: str | None, since: str | None, limit: int) -> None:
    """Query audit log events with filters."""
    from bernstein.core.audit import AuditLog

    if not AUDIT_DIR.is_dir():
        console.print(f"[red]Audit directory not found:[/red] {AUDIT_DIR}")
        raise SystemExit(1)

    audit_log = AuditLog(AUDIT_DIR)
    events = audit_log.query(event_type=event_type, actor=actor, since=since)
    events = events[:limit]

    if not events:
        console.print("[yellow]No matching audit events found.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta", show_lines=False)
    table.add_column("Timestamp", style="dim", no_wrap=True)
    table.add_column("Event Type", style="bold")
    table.add_column("Actor")
    table.add_column("Resource")
    table.add_column("HMAC", style="dim", no_wrap=True)

    for ev in events:
        table.add_row(
            ev.timestamp[:19],
            ev.event_type,
            ev.actor,
            f"{ev.resource_type}/{ev.resource_id}",
            ev.hmac[:12] + "…",
        )

    console.print()
    console.print(table)
    console.print(f"\n[dim]Showing {len(events)} event(s)[/dim]\n")
