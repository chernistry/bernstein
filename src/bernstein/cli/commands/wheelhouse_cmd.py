"""``bernstein wheelhouse`` -- operator-facing wrappers for air-gap utilities.

Two subcommands:

* ``bernstein wheelhouse build``  -- builds an air-gap wheel bundle by
  invoking the same code path as ``scripts/build_airgap_wheelhouse.py``.
  Customers do not need to know the script exists.
* ``bernstein wheelhouse verify`` -- walks every wheel in a bundle,
  recomputes sha256s against ``MANIFEST.json``, and validates detached
  signatures using the chosen :class:`WheelhouseVerifier` backend
  (``--verifier auto|crypto|cosign|gpg``).

The verify subcommand is also exposed at ``bernstein verify <path>``
for back-compat with the Phase 1 entry point. Both surfaces share
the same core implementation in :mod:`bernstein.core.distribution`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.panel import Panel
from rich.table import Table

from bernstein.cli.helpers import console
from bernstein.core.distribution import (
    DEFAULT_TRUST_DIR,
    SIGSTORE_DEFAULT_OWNER,
    CustomerCountersignError,
    SigstoreAttestationVerifier,
    SigstoreBatchReport,
    VerifierKind,
    VerifyReport,
    countersign_bundle,
    select_verifier,
    verify_artefacts_with_sigstore,
    verify_wheelhouse,
)

if TYPE_CHECKING:
    from types import ModuleType


def _load_build_module() -> ModuleType:
    """Load ``scripts/build_airgap_wheelhouse.py`` by file path.

    The build script lives outside the package tree so customers can
    audit it standalone. Loading by absolute path lets the CLI invoke
    the same code path the script uses without sys.path manipulation.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "scripts" / "build_airgap_wheelhouse.py"
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("build_airgap_wheelhouse", candidate)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"could not load build script at {candidate}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("could not locate scripts/build_airgap_wheelhouse.py")


@click.group(name="wheelhouse")
def wheelhouse_group() -> None:
    """Build and verify the air-gap wheel bundle.

    \b
    Examples:
      bernstein wheelhouse build --version 1.9.4
      bernstein wheelhouse verify dist/airgap-wheelhouse/1.9.4
      bernstein wheelhouse verify <path> --verifier gpg --keyring keys.gpg
    """


@wheelhouse_group.command("build")
@click.option(
    "--version",
    "version",
    default=None,
    metavar="VERSION",
    help="Override the version label. Defaults to the value in pyproject.toml.",
)
@click.option(
    "--output",
    "output",
    default=None,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Output directory. Defaults to dist/airgap-wheelhouse/<version>/.",
)
@click.option(
    "--skip-project",
    "skip_project",
    is_flag=True,
    default=False,
    help="Skip building the bernstein wheel itself (used for representative bundles).",
)
def build_cmd(version: str | None, output: Path | None, skip_project: bool) -> None:
    """Build an air-gap wheel bundle for offline installs.

    \b
    Resolves the full pinned dependency closure via uv export, downloads
    every wheel into the bundle, builds the bernstein wheel, and writes
    a deterministic MANIFEST.json with sha256 entries. Run
    ``bernstein wheelhouse verify <path>`` afterwards to validate.
    """
    console.print()
    console.print(Panel("[bold]Building air-gap wheelhouse[/bold]", border_style="blue", expand=False))

    try:
        module = _load_build_module()
        result = module.build(version=version, output=output, skip_project=skip_project)
    except Exception as exc:
        console.print(
            Panel(
                f"[bold red]Wheelhouse build failed:[/bold red] {exc}",
                border_style="red",
                expand=False,
            )
        )
        raise SystemExit(1) from exc

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=14)
    table.add_column("Value")
    table.add_row("Version", result.version)
    table.add_row("Output", str(result.output_dir))
    table.add_row("Wheels", str(len(result.wheels)))
    table.add_row("Manifest", str(result.manifest_path) if result.manifest_path else "(none)")
    console.print(table)
    console.print(
        f"\n  [dim]Sign with:[/dim] COSIGN_KEY=cosign.key bash scripts/sign_airgap_wheelhouse.sh {result.output_dir}"
    )
    console.print(f"  [dim]Verify with:[/dim] bernstein wheelhouse verify {result.output_dir}\n")


@wheelhouse_group.command("verify")
@click.argument(
    "wheelhouse_path",
    required=True,
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--verifier",
    "verifier_kind",
    type=click.Choice([k.value for k in VerifierKind], case_sensitive=False),
    default=VerifierKind.AUTO.value,
    help="Signature backend. 'crypto' uses a PEM key, 'cosign' shells to the cosign CLI, "
    "'gpg' shells to gpg/gpg2. 'auto' picks the best available given the inputs.",
)
@click.option(
    "--ca-pubkey",
    "ca_pubkey",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Public key (PEM) for crypto/cosign verifiers.",
)
@click.option(
    "--keyring",
    "keyring_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="GPG keyring path (used by --verifier gpg).",
)
@click.option(
    "--cosign-identity",
    "cosign_identity",
    default=None,
    metavar="IDENTITY",
    help="Sigstore certificate identity (cosign keyless mode).",
)
@click.option(
    "--cosign-issuer",
    "cosign_issuer",
    default=None,
    metavar="ISSUER",
    help="Sigstore OIDC issuer (cosign keyless mode).",
)
@click.option(
    "--require-signatures/--no-require-signatures",
    "require_signatures",
    default=False,
    help="Exit non-zero if any wheel is missing a signature file.",
)
@click.option(
    "--require-customer-sig/--no-require-customer-sig",
    "require_customer_sig",
    default=False,
    help="Exit non-zero if MANIFEST.customer.sig is missing or unverified. "
    "Use this when a sovereign customer requires a two-key chain "
    "(org cosign + customer Ed25519 countersignature).",
)
@click.option(
    "--customer-trust-dir",
    "customer_trust_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help=f"Directory of allowed customer Ed25519 public keys. Defaults to {DEFAULT_TRUST_DIR}.",
)
@click.option(
    "--sigstore/--no-sigstore",
    "sigstore",
    default=False,
    help="Additively verify Sigstore build-provenance attestations (`actions/attest-build-provenance`) "
    "for every wheel via `gh attestation verify`. Default behaviour (cosign + GPG fallback) is unchanged "
    "when this flag is off.",
)
@click.option(
    "--sigstore-owner",
    "sigstore_owner",
    default=SIGSTORE_DEFAULT_OWNER,
    metavar="OWNER",
    help=f"GitHub owner whose attestations are accepted. Defaults to {SIGSTORE_DEFAULT_OWNER!r}.",
)
@click.option(
    "--sigstore-repo",
    "sigstore_repo",
    default=None,
    metavar="OWNER/REPO",
    help="Optional repo to pin attestations to (e.g. sipyourdrink-ltd/bernstein).",
)
@click.option(
    "--sigstore-offline/--no-sigstore-offline",
    "sigstore_offline",
    default=False,
    help="Verify against a local .sigstore bundle (next to the artefact or in --sigstore-bundle-dir) "
    "instead of contacting the GitHub attestations endpoint. Air-gap-friendly path.",
)
@click.option(
    "--sigstore-bundle-dir",
    "sigstore_bundle_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory of pre-downloaded .sigstore bundles for offline verification.",
)
@click.option(
    "--require-sigstore/--no-require-sigstore",
    "require_sigstore",
    default=False,
    help="Promote a missing attestation to a hard failure. Implies --sigstore.",
)
def verify_subcmd(
    wheelhouse_path: Path,
    verifier_kind: str,
    ca_pubkey: Path | None,
    keyring_path: Path | None,
    cosign_identity: str | None,
    cosign_issuer: str | None,
    require_signatures: bool,
    require_customer_sig: bool,
    customer_trust_dir: Path | None,
    sigstore: bool,
    sigstore_owner: str,
    sigstore_repo: str | None,
    sigstore_offline: bool,
    sigstore_bundle_dir: Path | None,
    require_sigstore: bool,
) -> None:
    """Verify an air-gap wheelhouse: sha256s + signatures, every wheel."""
    exit_code = run_verify(
        wheelhouse_path=wheelhouse_path,
        verifier_kind=verifier_kind,
        ca_pubkey=ca_pubkey,
        keyring_path=keyring_path,
        cosign_identity=cosign_identity,
        cosign_issuer=cosign_issuer,
        require_signatures=require_signatures,
        require_customer_sig=require_customer_sig,
        customer_trust_dir=customer_trust_dir,
        sigstore=sigstore or require_sigstore,
        sigstore_owner=sigstore_owner,
        sigstore_repo=sigstore_repo,
        sigstore_offline=sigstore_offline,
        sigstore_bundle_dir=sigstore_bundle_dir,
        require_sigstore=require_sigstore,
    )
    raise SystemExit(exit_code)


def run_verify(
    *,
    wheelhouse_path: Path,
    verifier_kind: str,
    ca_pubkey: Path | None,
    keyring_path: Path | None,
    cosign_identity: str | None,
    cosign_issuer: str | None,
    require_signatures: bool,
    require_customer_sig: bool = False,
    customer_trust_dir: Path | None = None,
    sigstore: bool = False,
    sigstore_owner: str = SIGSTORE_DEFAULT_OWNER,
    sigstore_repo: str | None = None,
    sigstore_offline: bool = False,
    sigstore_bundle_dir: Path | None = None,
    require_sigstore: bool = False,
) -> int:
    """Shared verify implementation used by both ``wheelhouse verify``
    and the legacy ``bernstein verify <path>`` entry point.

    The Sigstore attestation chain (when *sigstore* is on) runs after
    the cosign / GPG / PEM-key flow. A non-zero base exit-code is
    preserved -- Sigstore can only escalate the failure list, never
    mask one.
    """
    verifier = select_verifier(
        verifier_kind,
        pubkey_path=ca_pubkey,
        keyring_path=keyring_path,
        cosign_identity=cosign_identity,
        cosign_issuer=cosign_issuer,
    )
    report = verify_wheelhouse(
        wheelhouse_path,
        verifier=verifier,
        require_signatures=require_signatures,
        require_customer_sig=require_customer_sig,
        customer_trust_dir=customer_trust_dir,
    )
    _render_verify(report, wheelhouse_path)
    base_exit = 0 if report.ok else 1

    if sigstore:
        wheels = sorted(wheelhouse_path.glob("*.whl"))
        sig_verifier = SigstoreAttestationVerifier(
            owner=sigstore_owner,
            repo=sigstore_repo,
            offline=sigstore_offline,
            bundle_dir=sigstore_bundle_dir,
        )
        sig_report = verify_artefacts_with_sigstore(
            wheels,
            verifier=sig_verifier,
            require_attestation=require_sigstore,
        )
        _render_sigstore(sig_report, owner=sigstore_owner)
        if sig_report.ok is False:
            base_exit = 1

    return base_exit


@wheelhouse_group.command("countersign")
@click.option(
    "--in",
    "in_path_arg",
    required=True,
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    help="Wheelhouse directory to countersign.",
)
@click.option(
    "--key",
    "key_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Customer Ed25519 PRIVATE key (PEM PKCS#8 or raw 32 bytes).",
)
@click.option(
    "--out",
    "out_path",
    default=None,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Optional output directory. Defaults to in-place countersign.",
)
@click.option(
    "--org-name",
    "org_name",
    default=None,
    metavar="ORG",
    help="Logical org name embedded in MANIFEST.customer.json. Defaults to the key file's stem.",
)
def countersign_subcmd(
    in_path_arg: Path,
    key_path: Path,
    out_path: Path | None,
    org_name: str | None,
) -> None:
    """Append a customer Ed25519 countersignature to an existing wheelhouse.

    \b
    The bundle must already carry the org cosign signatures
    (MANIFEST.sig + per-wheel .sig). This command produces:

      <bundle>/MANIFEST.customer.sig    -- detached Ed25519 signature
      <bundle>/MANIFEST.customer.json   -- {org_name, alg, public_key_pem, ...}

    Verify the result with:

      bernstein wheelhouse verify <bundle> --require-customer-sig
    """
    console.print()
    try:
        sig_path = countersign_bundle(
            in_path_arg,
            customer_key_path=key_path,
            org_name=org_name,
            out_path=out_path,
        )
    except CustomerCountersignError as exc:
        console.print(
            Panel(
                f"[bold red]Customer countersign failed:[/bold red] {exc}",
                border_style="red",
                expand=False,
            )
        )
        raise SystemExit(1) from exc

    console.print(
        Panel(
            "[bold green]Customer countersign: WROTE[/bold green]",
            border_style="green",
            expand=False,
        )
    )
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=22)
    table.add_column("Value")
    table.add_row("Bundle", str(out_path or in_path_arg))
    table.add_row("Signature", str(sig_path))
    table.add_row("Org", org_name or key_path.stem)
    console.print(table)
    console.print(
        f"\n  [dim]Verify with:[/dim] bernstein wheelhouse verify {out_path or in_path_arg} --require-customer-sig\n"
    )


def _render_verify(report: VerifyReport, wheelhouse_path: Path) -> None:
    """Pretty-print the verify outcome to the operator's terminal."""
    console.print()
    if report.ok:
        console.print(
            Panel(
                "[bold green]Wheelhouse Verify: PASSED[/bold green]",
                border_style="green",
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]Wheelhouse Verify: FAILED[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        for err in report.failures:
            console.print(f"  [red]![/red] {err}")
        console.print()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=22)
    table.add_column("Value")
    table.add_row("Path", str(wheelhouse_path))
    table.add_row("Verifier", report.verifier)
    table.add_row("Wheels verified", f"{report.wheels_verified} / {report.wheels_total}")
    table.add_row("Signatures present", str(report.signatures_present))
    table.add_row("Signatures verified", str(report.signatures_verified))
    if report.manifest_signature_ok is True:
        table.add_row("MANIFEST.sig", "[green]ok[/green]")
    elif report.manifest_signature_ok is False:
        table.add_row("MANIFEST.sig", "[red]invalid[/red]")
    else:
        table.add_row("MANIFEST.sig", "(absent)")
    if report.customer_signature_ok is True:
        org = report.customer_org or "?"
        table.add_row("Customer sig", f"[green]ok[/green] (org={org})")
    elif report.customer_signature_ok is False:
        table.add_row("Customer sig", "[red]invalid[/red]")
    elif report.customer_signature_present:
        table.add_row("Customer sig", "[yellow]present, unverified[/yellow]")
    else:
        table.add_row("Customer sig", "(absent)")
    console.print(table)
    console.print()


def _render_sigstore(report: SigstoreBatchReport, *, owner: str) -> None:
    """Pretty-print the Sigstore-attestation outcome to the operator's terminal."""
    console.print()
    if not report.verifier_available:
        console.print(
            Panel(
                "[bold yellow]Sigstore Verify: SKIPPED[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        console.print("  [yellow]![/yellow] gh CLI not on PATH -- install GitHub CLI to opt in")
        for skip in report.skips:
            console.print(f"  [dim]-[/dim] {skip}")
        for fail in report.failures:
            console.print(f"  [red]![/red] {fail}")
        console.print()
        return

    if report.ok is True:
        console.print(
            Panel(
                "[bold green]Sigstore Verify: PASSED[/bold green]",
                border_style="green",
                expand=False,
            )
        )
    elif report.ok is False:
        console.print(
            Panel(
                "[bold red]Sigstore Verify: FAILED[/bold red]",
                border_style="red",
                expand=False,
            )
        )
        for fail in report.failures:
            console.print(f"  [red]![/red] {fail}")
    else:
        console.print(
            Panel(
                "[bold yellow]Sigstore Verify: ADVISORY[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        for skip in report.skips:
            console.print(f"  [dim]-[/dim] {skip}")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="dim", no_wrap=True, min_width=22)
    table.add_column("Value")
    table.add_row("Owner", owner)
    table.add_row("Artefacts attested", str(report.passes))
    table.add_row("Failures", str(len(report.failures)))
    table.add_row("Skipped", str(len(report.skips)))
    console.print(table)
    console.print()
