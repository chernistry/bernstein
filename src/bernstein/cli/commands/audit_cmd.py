"""Audit CLI -- HMAC-chain integrity, Merkle seal, and evidence export.

Bernstein keeps a tamper-evident, append-only audit log under
``.sdd/audit/YYYY-MM-DD.jsonl``. Every record is HMAC-SHA256-signed
(RFC 2104) and chained to the previous record's HMAC, so any after-the-fact
edit invalidates every following entry. The signing key sits outside the
audit volume; daily files share one chain; ``bernstein audit verify``
replays the chain and exits non-zero on any break.

Commands:
  bernstein audit show               Show recent audit log events.
  bernstein audit seal               Compute and store a Merkle root.
  bernstein audit seal --anchor-git  Also create a git tag.
  bernstein audit verify             Verify HMAC chain and Merkle tree.
  bernstein audit verify --hmac-only Verify HMAC chain only.
  bernstein audit verify --merkle-only  Verify Merkle tree only.
  bernstein audit verify-hmac        Verify HMAC chain across all audit files.
  bernstein audit export             Export a signed Article 12 evidence pack.
  bernstein audit pack               Build a SOC 2 evidence checklist.
  bernstein audit capabilities       Print lethal-trifecta capability matrix.
  bernstein audit slice              Write a deterministic subset (KF-5).
  bernstein audit query              Query audit log events with filters.

Operator guide: docs/security/audit-log.md.
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
    """Audit log integrity tools (RFC 2104 HMAC chain + Merkle seal).

    See docs/security/audit-log.md for the operator runbook.
    """


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
    """Verify audit log integrity (HMAC chain per RFC 2104 + Merkle tree).

    \b
      bernstein audit verify              Verify both HMAC chain and Merkle tree
      bernstein audit verify --hmac-only  Verify HMAC chain only
      bernstein audit verify --merkle-only  Verify Merkle tree only

    Exits non-zero on any chain break, missing record, or HMAC mismatch.
    Run from cron and fail the run on non-zero exit (cite: docs/security/audit-log.md).
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
    "--tenant",
    "tenant",
    default=None,
    help=(
        "Emit a multi-tenant audit-chain export scoped to <tenant_id>. "
        "Combine with --since/--until. Bundle conforms to "
        "schemas/audit-multitenant-export-v2.json (back-compat with v1)."
    ),
)
@click.option(
    "--since",
    default=None,
    help="ISO-8601 inclusive lower bound (Article 12 / tenant mode).",
)
@click.option(
    "--until",
    default=None,
    help="ISO-8601 exclusive upper bound (Article 12 / tenant mode).",
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
    "--signature-kind",
    "signature_kind",
    default="hmac-chain-only",
    type=click.Choice(
        [
            "hmac-chain-only",
            "hmac-chain+rfc3161",
            "hmac-chain+offline-anchor",
            "hmac-chain+pubkey",
            "hmac-chain+rfc3161+pubkey",
        ],
    ),
    show_default=True,
    help=(
        "Tenant mode: which detached anchor to attach. "
        "'hmac-chain-only' is the bare HMAC chain. "
        "'hmac-chain+rfc3161' attaches a TSA timestamp token (--rfc3161-token). "
        "'hmac-chain+offline-anchor' is an air-gap fallback (deterministic local anchor). "
        "'hmac-chain+pubkey' (v2) signs head_sha256 with the lineage Ed25519 key "
        "so a key-less auditor can authenticate the bundle. "
        "'hmac-chain+rfc3161+pubkey' (v2) attaches both."
    ),
)
@click.option(
    "--rfc3161-token",
    "rfc3161_token",
    default=None,
    help=(
        "Tenant mode: path to a base64-encoded DER RFC 3161 TimeStampToken "
        "(required iff --signature-kind contains 'rfc3161')."
    ),
)
@click.option(
    "--rfc3161-tsa-url",
    "rfc3161_tsa_url",
    default=None,
    help="Tenant mode: URL of the TSA that issued the token (informational).",
)
@click.option(
    "--head-signing-key-path",
    "head_signing_key_path",
    default=None,
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
    help=(
        "Tenant mode (v2): path to the Ed25519 private key (PEM PKCS#8 or "
        "raw 32-byte) used to sign head_sha256. Required iff "
        "--signature-kind contains 'pubkey' and --head-signing-env-var is "
        "not supplied. Reuses the lineage signer key (see "
        "src/bernstein/core/security/lineage_kms.py)."
    ),
)
@click.option(
    "--head-signing-env-var",
    "head_signing_env_var",
    default=None,
    help=(
        "Tenant mode (v2): env var carrying a PEM Ed25519 private key. Mutually exclusive with --head-signing-key-path."
    ),
)
@click.option(
    "--head-signing-key-id",
    "head_signing_key_id",
    default=None,
    help="Tenant mode (v2): operator-stable JWK 'kid' for the head signature.",
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
    help="Article 12 / tenant modes: build the bundle in-memory and print the manifest without writing to disk.",
)
@click.option("--dir", "workdir", default=".", show_default=True, help="Project root directory.")
def export_cmd(
    period: str | None,
    article_12: bool,
    tenant: str | None,
    since: str | None,
    until: str | None,
    risk_class: str,
    fmt: str,
    signature_kind: str,
    rfc3161_token: str | None,
    rfc3161_tsa_url: str | None,
    head_signing_key_path: str | None,
    head_signing_env_var: str | None,
    head_signing_key_id: str | None,
    output: str | None,
    dry_run: bool,
    workdir: str,
) -> None:
    """Export an evidence package for auditors.

    \b
    Three modes:
      * SOC 2 mode (default): bernstein audit export --period Q1-2026
      * EU AI Act Article 12: bernstein audit export --article-12 \
            --since 2026-08-01T00:00:00+00:00 --until 2026-09-01T00:00:00+00:00
      * Multi-tenant slice:   bernstein audit export --tenant acme \
            --since ... --until ... [--signature-kind ...]

    \b
    SOC 2 mode collects audit logs, HMAC verification (RFC 2104), Merkle
    seals, compliance config, WAL entries, and SBOM into a single package.

    \b
    Article 12 mode emits a deterministic, retention-pinned bundle for
    EU AI Act high-risk-system record-keeping (Article 12 of Regulation
    (EU) 2024/1689): audit log slice, data-governance catalog, and an
    EU-AI-Act clause map. manifest.json contains artefact SHA-256 hashes
    for auditor verification.

    \b
    Multi-tenant mode emits a deterministic JSON bundle per the schema at
    schemas/audit-multitenant-export-v1.json: events tagged with the
    given tenant_id are filtered, then re-chained over a slice-local
    HMAC so an external auditor can replay-verify offline. Cross-tenant
    leakage is blocked at filter time and detected at verify time.
    """
    sdd_dir = Path(workdir).resolve() / ".sdd"
    if not sdd_dir.is_dir():
        console.print(f"[red]State directory not found:[/red] {sdd_dir}")
        console.print("[dim]Run [bold]bernstein run[/bold] first to generate audit data.[/dim]")
        raise SystemExit(1)

    if tenant:
        _run_tenant_export(
            sdd_dir=sdd_dir,
            tenant_id=tenant,
            since=since,
            until=until,
            signature_kind=signature_kind,
            rfc3161_token=rfc3161_token,
            rfc3161_tsa_url=rfc3161_tsa_url,
            head_signing_key_path=head_signing_key_path,
            head_signing_env_var=head_signing_env_var,
            head_signing_key_id=head_signing_key_id,
            output=output,
            dry_run=dry_run,
        )
        return

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
            "[red]One of --period (SOC 2), --article-12, or --tenant is required.[/red]",
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


def _run_tenant_export(
    *,
    sdd_dir: Path,
    tenant_id: str,
    since: str | None,
    until: str | None,
    signature_kind: str,
    rfc3161_token: str | None,
    rfc3161_tsa_url: str | None,
    head_signing_key_path: str | None,
    head_signing_env_var: str | None,
    head_signing_key_id: str | None,
    output: str | None,
    dry_run: bool,
) -> None:
    """Execute the multi-tenant audit-chain export flow.

    Loads the operator HMAC key from the canonical key path used by
    :class:`bernstein.core.security.audit.AuditLog`, scopes the bundle
    to ``tenant_id``, and writes a deterministic JSON bundle conforming
    to ``schemas/audit-multitenant-export-v2.json``. When the operator
    selects a ``+pubkey`` signature kind, the lineage KMS adapter
    (file-based or env-based) is also wired up to sign ``head_sha256``.
    """
    import json as _json
    from typing import cast

    from bernstein.core.security.audit import load_or_create_audit_key
    from bernstein.core.security.audit_multitenant import (
        SignatureKind,
        TenantScopedExport,
        export_tenant_slice,
    )
    from bernstein.core.security.lineage_kms import (
        EnvBasedKMSAdapter,
        FileBasedKMSAdapter,
        KMSAdapter,
    )

    if not since or not until:
        console.print("[red]--tenant requires both --since and --until (ISO-8601).[/red]")
        raise SystemExit(2)

    rfc3161_token_b64: str | None = None
    if rfc3161_token:
        token_path = Path(rfc3161_token).expanduser()
        if not token_path.is_file():
            console.print(f"[red]--rfc3161-token file not found: {token_path}[/red]")
            raise SystemExit(1)
        rfc3161_token_b64 = token_path.read_text(encoding="utf-8").strip()

    if "rfc3161" in signature_kind and not rfc3161_token_b64:
        console.print(
            f"[red]--signature-kind={signature_kind} requires --rfc3161-token <path>.[/red]",
        )
        raise SystemExit(2)

    head_kms_adapter: KMSAdapter | None = None
    if "pubkey" in signature_kind:
        if head_signing_key_path and head_signing_env_var:
            console.print(
                "[red]--head-signing-key-path and --head-signing-env-var are mutually exclusive.[/red]",
            )
            raise SystemExit(2)
        from bernstein.core.persistence.lineage_signer import LineageSignerError

        try:
            if head_signing_key_path:
                head_kms_adapter = FileBasedKMSAdapter(
                    Path(head_signing_key_path),
                    kid=head_signing_key_id,
                )
            elif head_signing_env_var:
                head_kms_adapter = EnvBasedKMSAdapter(
                    head_signing_env_var,
                    kid=head_signing_key_id,
                )
            else:
                console.print(
                    f"[red]--signature-kind={signature_kind} requires either "
                    "--head-signing-key-path or --head-signing-env-var.[/red]",
                )
                raise SystemExit(2)
        except (LineageSignerError, OSError, ValueError) as exc:
            console.print(f"[red]Failed to load head signing key: {exc}[/red]")
            raise SystemExit(1) from None

    audit_dir = sdd_dir / "audit"
    output_dir = Path(output).resolve() if output else None

    try:
        key = load_or_create_audit_key()
    except OSError as exc:  # pragma: no cover - filesystem race
        console.print(f"[red]Failed to load audit key: {exc}[/red]")
        raise SystemExit(1) from None

    try:
        export: TenantScopedExport = export_tenant_slice(
            audit_dir=audit_dir,
            tenant_id=tenant_id,
            since=since,
            until=until,
            key=key,
            output_dir=output_dir,
            signature_kind=cast("SignatureKind", signature_kind),
            rfc3161_token_b64=rfc3161_token_b64,
            rfc3161_tsa_url=rfc3161_tsa_url,
            head_kms_adapter=head_kms_adapter,
            write=not dry_run,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None

    console.print()
    console.print(
        Panel(
            "[bold]Multi-tenant Audit Slice[/bold]",
            border_style="green",
            expand=False,
        ),
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=18)
    table.add_column("Value")
    table.add_row("Tenant", export.tenant_id)
    table.add_row("Window", f"{export.since} → {export.until}")
    table.add_row("Events", str(export.event_count))
    table.add_row("Head HMAC", export.head_hmac[:16] + "…")
    table.add_row("Head SHA-256", export.head_sha256[:16] + "…")
    table.add_row("Signature kind", export.signature_kind)
    if export.bundle_path is not None:
        table.add_row("Bundle", str(export.bundle_path))
    elif dry_run:
        table.add_row("Bundle", "(dry-run, not written)")
    console.print(table)
    console.print()

    if dry_run:
        console.print("[dim]Bundle (dry-run):[/dim]")
        console.print(_json.dumps(_json.loads(export.bundle_bytes.decode("utf-8")), indent=2))
        console.print()


@audit_group.command("verify-multitenant")
@click.option(
    "--bundle",
    "bundle_path",
    required=True,
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
    help="Path to a multi-tenant audit-export bundle (JSON) to verify.",
)
@click.option(
    "--rfc3161-trusted-tsa-bundle",
    "rfc3161_trust_bundle",
    default=None,
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
    help=(
        "Path to a PEM/DER X.509 trust bundle (operator-supplied TSA roots "
        "+ intermediates). Enables RFC 3161 cryptographic chain validation. "
        "Without this flag, the verifier confirms the token is well-formed "
        "base64 but does NOT validate the TSA chain."
    ),
)
@click.option(
    "--head-signing-public-jwk",
    "head_signing_public_jwk_path",
    default=None,
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
    help=(
        "Path to a JSON file containing the trusted Ed25519 verifier JWK "
        "(RFC 8037 OKP form). When supplied, the bundle's embedded JWK "
        "must match before the head_signature is trusted."
    ),
)
def verify_multitenant_cmd(
    bundle_path: str,
    rfc3161_trust_bundle: str | None,
    head_signing_public_jwk_path: str | None,
) -> None:
    """Verify a multi-tenant audit-export bundle offline.

    \b
    Runs the full v2 verifier:
      1. Envelope structure + tenant purity + HMAC chain integrity.
      2. SHA-256 anchor consistency (catches single-byte flips).
      3. (opt) RFC 3161 cryptographic chain validation when
         --rfc3161-trusted-tsa-bundle is supplied.
      4. (opt) Ed25519 signature over head_sha256 when the bundle
         carries a head_signature block. Pass
         --head-signing-public-jwk to pin the verifier key.

    Exits non-zero on any failure; prints every observed error.
    """
    import json as _json

    from bernstein.core.security.audit import load_or_create_audit_key
    from bernstein.core.security.audit_multitenant import verify_tenant_slice

    try:
        hmac_key = load_or_create_audit_key()
    except OSError as exc:
        console.print(f"[red]Failed to load audit key: {exc}[/red]")
        raise SystemExit(1) from None

    trusted_tsa_certs = None
    if rfc3161_trust_bundle:
        from bernstein.core.security.rfc3161_verifier import load_trusted_tsa_certs

        try:
            trusted_tsa_certs = load_trusted_tsa_certs(Path(rfc3161_trust_bundle))
        except ValueError as exc:
            console.print(f"[red]Trust bundle load failed: {exc}[/red]")
            raise SystemExit(1) from None

    trusted_jwk: dict | None = None
    if head_signing_public_jwk_path:
        try:
            trusted_jwk = _json.loads(
                Path(head_signing_public_jwk_path).read_text(encoding="utf-8"),
            )
        except (OSError, _json.JSONDecodeError) as exc:
            console.print(f"[red]Trusted JWK load failed: {exc}[/red]")
            raise SystemExit(1) from None
        if not isinstance(trusted_jwk, dict):
            console.print("[red]Trusted JWK must be a JSON object.[/red]")
            raise SystemExit(1)

    result = verify_tenant_slice(
        Path(bundle_path),
        key=hmac_key,
        rfc3161_trusted_tsa_certs=trusted_tsa_certs,
        head_signature_trusted_jwk=trusted_jwk,
    )

    console.print()
    if result.ok:
        console.print(
            Panel(
                "[bold green]Multi-tenant Audit Slice Verified[/bold green]",
                border_style="green",
                expand=False,
            ),
        )
        bundle = result.bundle
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Key", style="dim", no_wrap=True, min_width=18)
        table.add_column("Value")
        table.add_row("Schema version", str(bundle.get("schema_version", "?")))
        table.add_row("Tenant", str(bundle.get("tenant_id", "?")))
        table.add_row("Events", str(bundle.get("event_count", "?")))
        table.add_row(
            "Signature kind",
            str((bundle.get("signature") or {}).get("signature_kind", "?")),
        )
        rfc3161_state = (
            "verified"
            if trusted_tsa_certs
            and "rfc3161"
            in str(
                (bundle.get("signature") or {}).get("signature_kind", ""),
            )
            else "skipped"
        )
        table.add_row("RFC 3161", rfc3161_state)
        head_sig_state = "verified" if bundle.get("head_signature") else "absent"
        table.add_row("Head signature", head_sig_state)
        console.print(table)
        console.print()
        return

    console.print(
        Panel(
            "[bold red]Multi-tenant Audit Slice FAILED[/bold red]",
            border_style="red",
            expand=False,
        ),
    )
    for err in result.errors:
        console.print(f"  [red]![/red] {err}")
    console.print()
    raise SystemExit(1)


@audit_group.command("pack")
@click.option(
    "--soc2",
    "soc2",
    is_flag=True,
    default=False,
    help="Emit a SOC 2 evidence-checklist Markdown pack with per-control evidence references.",
)
@click.option(
    "--include-runs",
    "include_runs",
    default=None,
    help="ISO-8601 timestamp; only include runs newer than this in the run-log evidence row.",
)
@click.option(
    "--period-label",
    "period_label",
    default="current",
    show_default=True,
    help="Human-readable period label rendered into the markdown header.",
)
@click.option(
    "--stale-after-days",
    "stale_after_days",
    default=30,
    show_default=True,
    type=int,
    help="Mark sources whose mtime is older than this window as STALE.",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output directory (defaults to .sdd/evidence/soc2/).",
)
@click.option("--workdir", "workdir", default=".", show_default=True, help="Project root directory.")
def pack_cmd(
    soc2: bool,
    include_runs: str | None,
    period_label: str,
    stale_after_days: int,
    output: str | None,
    workdir: str,
) -> None:
    """Build a SOC 2 evidence checklist with real per-control evidence refs.

    \b
    Each Trust Service Criteria row in the output Markdown carries a
    concrete pointer (path on disk, sha256 hash, or pending marker).
    Drives auditor walkthroughs without copy-pasting from JSON.
    """
    if not soc2:
        console.print("[red]bernstein audit pack currently supports only --soc2.[/red]")
        raise SystemExit(2)

    from datetime import UTC, datetime

    from bernstein.core.security.audit_pack import generate_audit_pack

    since: datetime | None = None
    if include_runs:
        try:
            cleaned = include_runs.replace("Z", "+00:00") if include_runs.endswith("Z") else include_runs
            since = datetime.fromisoformat(cleaned)
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
        except ValueError as exc:
            console.print(f"[red]Invalid --include-runs timestamp: {exc}[/red]")
            raise SystemExit(2) from None

    output_path = Path(output).resolve() if output else None

    result = generate_audit_pack(
        workdir=Path(workdir).resolve(),
        output_dir=output_path,
        period_label=period_label,
        include_since=since,
        stale_after_days=stale_after_days,
        write=True,
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=14)
    table.add_column("Value")
    ok_count = sum(1 for r in result.resolved if r.status == "OK")
    pending_count = sum(1 for r in result.resolved if r.status == "PENDING")
    stale_count = sum(1 for r in result.resolved if r.status == "STALE")
    table.add_row("Period", period_label)
    table.add_row("Sources", str(len(result.resolved)))
    table.add_row("OK / Pending / Stale", f"{ok_count} / {pending_count} / {stale_count}")
    if result.markdown_path is not None:
        table.add_row("Markdown", str(result.markdown_path))
    if result.manifest_path is not None:
        table.add_row("Manifest", str(result.manifest_path))

    console.print()
    console.print(Panel("[bold]SOC 2 Evidence Pack[/bold]", border_style="green", expand=False))
    console.print(table)
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


@audit_group.command("slice")
@click.option(
    "--from",
    "from_hmac",
    default=None,
    help="Inclusive lower bound — HMAC of the first event to include.  Omit to start at the earliest recorded event.",
)
@click.option(
    "--to",
    "to_hmac",
    default=None,
    help="Inclusive upper bound — HMAC of the last event to include.  Omit to run through the latest event.",
)
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    help="Path to the output JSONL file.",
)
def slice_cmd(from_hmac: str | None, to_hmac: str | None, output: str) -> None:
    """Write a deterministic subset of the audit log between two HMACs.

    \b
    Foundation for KF-5 time-travel replay.  The output is byte-stable
    JSONL — each line is sort-keys-serialised — so downstream replayers
    can hash the slice directly.  The HMAC chain inside the slice is
    re-verified before writing; a structural mismatch aborts the export.

    \b
    Examples:
      bernstein audit slice --from <hash> --to <hash> -o /tmp/slice.jsonl
      bernstein audit slice --to <hash> -o /tmp/head.jsonl
      bernstein audit slice --from <hash> -o /tmp/tail.jsonl
    """
    from bernstein.core.security.audit_slice import (
        AuditSliceError,
        slice_audit_log,
        verify_slice_chain,
        write_slice_jsonl,
    )

    if not AUDIT_DIR.is_dir():
        console.print(f"[red]Audit directory not found:[/red] {AUDIT_DIR}")
        raise SystemExit(1)

    try:
        result = slice_audit_log(AUDIT_DIR, from_hmac=from_hmac, to_hmac=to_hmac)
    except AuditSliceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None

    valid, errors = verify_slice_chain(result)
    if not valid:
        console.print(Panel("[bold red]Slice chain check FAILED[/bold red]", border_style="red", expand=False))
        for err in errors:
            console.print(f"  [red]![/red] {err}")
        raise SystemExit(1)

    out_path = write_slice_jsonl(result, Path(output))

    console.print()
    console.print(Panel("[bold]Audit slice written[/bold]", border_style="green", expand=False))
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=14)
    table.add_column("Value")
    table.add_row("Events", str(result.event_count))
    table.add_row("From", result.from_hmac or "(genesis)")
    table.add_row("To", result.to_hmac or "(latest)")
    table.add_row("Source files", ", ".join(result.source_files) or "—")
    table.add_row("Output", str(out_path))
    console.print(table)
    console.print()


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
