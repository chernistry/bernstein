"""bernstein-verify CLI entry-point.

Three subcommands, all exit 0 on PASS / 1 on FAIL:

  bernstein-verify chain <artefact_path> [--lineage-dir DIR]
      Verify the full parent-hash chain of a single artefact path against
      a `.sdd/lineage/` directory layout (raw log).

  bernstein-verify pack <bundle.zip>
      Verify a `bernstein compliance pack` ZIP end-to-end.

  bernstein-verify forks <artefact_path> [--lineage-dir DIR]
      Report unresolved forks for one artefact (CI use). Exit 1 if any
      open tip > 1 for that path.

Output convention (per ADR-009 §9.3):
  - Human summary on stdout (one-line PASS/FAIL + brief reasons).
  - Structured JSON on stderr for machine consumers.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import click

from bernstein_verify.verify import (
    VerifyResult,
    jcs_canonicalise,
    verify_jws_detached,
    verify_pack,
    walk_chain,
)


def _emit(result: VerifyResult, kind: str) -> int:
    """Write human summary to stdout + JSON to stderr; return exit code."""
    if result.ok:
        click.echo(f"PASS  {kind}: {result.stats}")
    else:
        click.echo(f"FAIL  {kind}: {len(result.errors)} error(s)")
        for err in result.errors[:10]:
            click.echo(f"  - {err}")
        if len(result.errors) > 10:
            click.echo(f"  ... and {len(result.errors) - 10} more")
    click.echo(
        json.dumps({"ok": result.ok, "kind": kind, "stats": result.stats, "errors": result.errors}),
        err=True,
    )
    return 0 if result.ok else 1


@click.group()
@click.version_option(package_name="bernstein-verify")
def cli() -> None:
    """Standalone auditor CLI for Bernstein lineage v1.

    Verifies Ed25519 JWS signatures and parent-hash chains without
    requiring a `bernstein` install. Works offline (air-gap).
    """


@cli.command("pack")
@click.argument("bundle", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def pack_cmd(bundle: Path) -> None:
    """Verify a compliance-pack ZIP (`bernstein compliance pack` output)."""
    result = verify_pack(bundle)
    sys.exit(_emit(result, kind="pack"))


def _load_lineage_dir_entries(
    lineage_dir: Path, artefact_path: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load entries for one artefact from a `.sdd/lineage/` raw directory.

    For v1 we read `log.jsonl` and filter by `artefact_path`. The
    `by-artefact/` projection is a rebuildable index, not a source of
    truth; we don't trust it here.
    """
    errors: list[str] = []
    log_path = lineage_dir / "log.jsonl"
    if not log_path.exists():
        errors.append(f"missing lineage log: {log_path}")
        return [], errors
    entries: list[dict[str, Any]] = []
    for lineno, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"log.jsonl:{lineno}: invalid JSON ({exc.msg})")
            continue
        if entry.get("artefact_path") == artefact_path:
            entries.append(entry)
    return entries, errors


def _verify_chain_entries(
    entries: list[dict[str, Any]],
    lineage_dir: Path,
) -> VerifyResult:
    """Walk + signature-verify a list of entries against on-disk sidecars."""
    errors: list[str] = []
    chain_ok, chain_errors = walk_chain(entries)
    errors.extend(chain_errors)

    # Sidecar layout (per ADR-009 §4):
    #   signatures/<hash[:2]>/<hash>/<entry_hash>.jws
    #   .sdd/agents/<agent-id>/card.json
    sig_failures = 0
    for e in entries:
        import hashlib

        entry_hash = "sha256:" + hashlib.sha256(jcs_canonicalise(e)).hexdigest()
        agent_id = e.get("agent_id", "")
        # Card lookup: walk up from lineage_dir to find .sdd/agents/.
        card_path = _find_agent_card(lineage_dir, agent_id)
        if card_path is None:
            errors.append(f"entry {entry_hash}: no Agent Card for {agent_id}")
            sig_failures += 1
            continue
        try:
            card = json.loads(card_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"entry {entry_hash}: cannot read card {card_path}: {exc}")
            sig_failures += 1
            continue

        expected_kid = e.get("agent_card_kid", "")
        if card.get("kid") != expected_kid:
            card_kid = card.get("kid")
            errors.append(
                f"entry {entry_hash}: kid mismatch (card={card_kid!r}, entry={expected_kid!r})"
            )
            sig_failures += 1
            continue

        artefact_hash = hashlib.sha256(e.get("artefact_path", "").encode()).hexdigest()
        jws_path = (
            lineage_dir / "signatures" / artefact_hash[:2] / artefact_hash / f"{entry_hash}.jws"
        )
        if not jws_path.exists():
            errors.append(f"entry {entry_hash}: missing signature {jws_path}")
            sig_failures += 1
            continue
        jws = jws_path.read_text(encoding="utf-8").strip()
        payload = jcs_canonicalise(e)
        pub = card.get("public_key_pem", "")
        if not isinstance(pub, str) or not verify_jws_detached(
            payload, jws, pub, expected_kid=expected_kid
        ):
            errors.append(f"entry {entry_hash}: signature verification failed")
            sig_failures += 1

    stats = {
        "entries": len(entries),
        "chain_ok": chain_ok,
        "signature_failures": sig_failures,
    }
    return VerifyResult(
        ok=chain_ok and sig_failures == 0 and not errors,
        errors=errors,
        stats=stats,
    )


def _find_agent_card(lineage_dir: Path, agent_id: str) -> Path | None:
    """Locate `.sdd/agents/<agent-id>/card.json` near the lineage dir.

    `lineage_dir` is typically `.sdd/lineage/`; agents live at
    `.sdd/agents/<id>/card.json`. We walk up one level and look.
    """
    if not agent_id:
        return None
    safe_id = agent_id.replace("/", "_").replace("..", "_")
    candidate = lineage_dir.parent / "agents" / safe_id / "card.json"
    if candidate.exists():
        return candidate
    return None


@cli.command("chain")
@click.argument("artefact_path")
@click.option(
    "--lineage-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(".sdd/lineage"),
    show_default=True,
)
def chain_cmd(artefact_path: str, lineage_dir: Path) -> None:
    """Verify the chain of a single artefact path against `.sdd/lineage/`."""
    entries, load_errors = _load_lineage_dir_entries(lineage_dir, artefact_path)
    if load_errors:
        result = VerifyResult(ok=False, errors=load_errors, stats={"entries": 0})
        sys.exit(_emit(result, kind="chain"))
    if not entries:
        result = VerifyResult(
            ok=False,
            errors=[f"no entries found for artefact_path={artefact_path!r}"],
            stats={"entries": 0},
        )
        sys.exit(_emit(result, kind="chain"))
    result = _verify_chain_entries(entries, lineage_dir)
    sys.exit(_emit(result, kind="chain"))


@cli.command("forks")
@click.argument("artefact_path")
@click.option(
    "--lineage-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(".sdd/lineage"),
    show_default=True,
)
def forks_cmd(artefact_path: str, lineage_dir: Path) -> None:
    """Report unresolved forks for one artefact (CI use)."""
    import hashlib

    entries, load_errors = _load_lineage_dir_entries(lineage_dir, artefact_path)
    if load_errors:
        result = VerifyResult(ok=False, errors=load_errors, stats={"entries": 0})
        sys.exit(_emit(result, kind="forks"))

    # Compute tips: entries that are not anyone's parent.
    by_hash: dict[str, dict[str, Any]] = {}
    for e in entries:
        h = "sha256:" + hashlib.sha256(jcs_canonicalise(e)).hexdigest()
        by_hash[h] = e
    parented: set[str] = set()
    for e in entries:
        for p in e.get("parent_hashes", []) or []:
            parented.add(p)
    tips = [h for h in by_hash if h not in parented]

    # Group by parent_hashes for fork detection (siblings sharing same parent).
    sibling_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for h, e in by_hash.items():
        key = tuple(e.get("parent_hashes", []) or [])
        sibling_groups[key].append(h)
    forks = {
        parents: hs for parents, hs in sibling_groups.items() if len(hs) > 1 and len(parents) <= 1
    }

    errors: list[str] = []
    if len(tips) > 1:
        errors.append(f"{len(tips)} open tips: {tips}")
    for parents, hs in forks.items():
        errors.append(f"fork at parent={list(parents)}: {hs}")

    result = VerifyResult(
        ok=not errors,
        errors=errors,
        stats={"entries": len(entries), "tips": len(tips), "forks": len(forks)},
    )
    sys.exit(_emit(result, kind="forks"))


if __name__ == "__main__":
    cli()
