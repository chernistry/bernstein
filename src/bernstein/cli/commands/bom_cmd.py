"""``bernstein bom`` -- emit and verify AI-BOM exports.

The AI Bill of Materials is a deterministic projection over the lineage
v2 chain, the cost ledger, the decision log and the adapter contract
YAMLs (see ``bernstein.core.compliance.ai_bom`` for the data model).

Commands:

* ``bernstein bom emit --run <id> [--format <fmt>] [--out <path>]``
  Read the run snapshot (or load a custom snapshot JSON via
  ``--snapshot``) and emit the encoded BOM. ``--format`` defaults to
  ``json`` and accepts ``json``, ``cyclonedx`` and ``spdx``.
* ``bernstein bom verify <path>`` -- structural verification report.

Both subcommands are pure projections / pure verifications: no network
calls, no writes outside the resolved output path. This matches the
"BOM is not a source of truth" invariant in issue #1371.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

import click


@click.group("bom")
def bom_group() -> None:
    """AI Bill of Materials -- model/prompt/tool provenance.

    \b
    Examples:
      bernstein bom emit --run 20260518-101010 --format cyclonedx
      bernstein bom emit --snapshot /tmp/run.json --format json --out bom.json
      bernstein bom verify ./bernstein-2.1.0.bom.json
    """


@bom_group.command("emit")
@click.option(
    "--run",
    "run_id",
    default=None,
    help="Bernstein run identifier. Reads .sdd/runs/<run>/bom_snapshot.json.",
)
@click.option(
    "--snapshot",
    "snapshot_path",
    default=None,
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
    help="Explicit snapshot JSON path. Mutually exclusive with --run.",
)
@click.option(
    "--format",
    "fmt",
    default="json",
    type=click.Choice(["json", "cyclonedx", "spdx"]),
    show_default=True,
    help="Output encoding. Defaults to Bernstein-native JSON.",
)
@click.option(
    "--out",
    "out_path",
    default=None,
    type=click.Path(dir_okay=False, resolve_path=True),
    help="Write encoded BOM to this path (default: stdout).",
)
@click.option(
    "--workdir",
    default=".",
    show_default=True,
    help="Project root (used to resolve .sdd/runs when --run is given).",
)
def emit_cmd(
    run_id: str | None,
    snapshot_path: str | None,
    fmt: str,
    out_path: str | None,
    workdir: str,
) -> None:
    """Emit an AI-BOM derived from an existing run snapshot."""
    from bernstein.core.compliance.ai_bom import (
        BOMError,
        encode_bom,
        generate_bom,
    )

    if run_id and snapshot_path:
        click.echo("error: --run and --snapshot are mutually exclusive", err=True)
        raise SystemExit(2)
    if not run_id and not snapshot_path:
        click.echo("error: one of --run or --snapshot is required", err=True)
        raise SystemExit(2)

    if run_id:
        snap_path = Path(workdir).resolve() / ".sdd" / "runs" / run_id / "bom_snapshot.json"
        if not snap_path.exists():
            click.echo(f"error: snapshot not found at {snap_path}", err=True)
            raise SystemExit(1)
    else:
        assert snapshot_path is not None
        snap_path = Path(snapshot_path)

    try:
        snapshot_raw: Any = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        click.echo(f"error: failed to load snapshot: {exc}", err=True)
        raise SystemExit(1) from None

    if not isinstance(snapshot_raw, dict):
        click.echo("error: snapshot must decode to a JSON object", err=True)
        raise SystemExit(1)

    snapshot: dict[str, Any] = cast("dict[str, Any]", snapshot_raw)

    try:
        bom = generate_bom(snapshot)
        payload = encode_bom(bom, fmt=fmt)
    except BOMError as exc:
        click.echo(f"error: {exc}", err=True)
        raise SystemExit(1) from None

    if out_path:
        Path(out_path).write_bytes(payload)
        click.echo(f"wrote {len(payload)} bytes to {out_path}")
        return

    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.write(b"\n")


@bom_group.command("verify")
@click.argument(
    "bom_path",
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Only emit exit code; suppress the verification report.",
)
def verify_cmd(bom_path: str, quiet: bool) -> None:
    """Verify a previously emitted AI-BOM."""
    from bernstein.core.compliance.ai_bom import verify_bom

    raw = Path(bom_path).read_bytes()
    report = verify_bom(raw)

    if quiet:
        raise SystemExit(0 if report.ok else 1)

    if report.ok:
        click.echo(f"PASS: checked {report.checked_count} element(s)")
    else:
        click.echo(f"FAIL: {len(report.errors)} error(s); checked {report.checked_count} element(s)")
        for err in report.errors:
            click.echo(f"  - {err}")
    raise SystemExit(0 if report.ok else 1)


# Optional helper for tests / other callers: serialise a snapshot dict into
# the BOM emit pipeline without going through Click's invocation machinery.
def emit_bom_from_snapshot(snapshot: dict[str, Any], fmt: str = "json") -> bytes:
    """Encode ``snapshot`` directly. Thin wrapper for non-CLI callers."""
    from bernstein.core.compliance.ai_bom import encode_bom, generate_bom

    return encode_bom(generate_bom(snapshot), fmt=fmt)


__all__ = ["bom_group", "emit_bom_from_snapshot"]
