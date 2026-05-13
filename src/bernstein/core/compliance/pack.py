"""One-command EU AI Act Article 12 evidence pack.

See ``docs/decisions/009-lineage-v1.md`` §8 for the design rationale.

Public surface:

* :func:`build_pack` — assemble a ZIP bundle for the
  ``(since, until, org)`` triple, signed by the operator key.

The pack's manifest follows the SLSA v1.1 provenance shape (a flat dict
with ``builder``, ``build_started_at``, ``build_finished_at``,
``input_hashes``, ``output_hash``) so external auditor tooling can
re-verify the bundle without depending on Bernstein internals.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import metadata
from typing import TYPE_CHECKING, Any

from bernstein.core.compliance.article12 import (
    ARTICLE12_PARAGRAPH_MAP,
    render_csv,
    render_pdf,
)
from bernstein.core.lineage.entry import LineageEntry, entry_hash
from bernstein.core.lineage.identity import sign_detached

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

__all__ = ["build_pack"]


_OPERATOR_KID = "operator-pack-signer"


def _date_to_ns_inclusive(d: date, *, end_of_day: bool = False) -> int:
    """Convert a calendar date to ns-since-epoch.

    If ``end_of_day`` is True, returns 23:59:59.999999999 UTC of that day
    so the window is inclusive on both sides.
    """
    if end_of_day:
        dt = datetime(d.year, d.month, d.day, 23, 59, 59, 999_999, tzinfo=UTC)
    else:
        dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
    base_ns = int(dt.timestamp() * 1_000_000_000)
    if end_of_day:
        base_ns += 999  # bump to make the boundary unambiguously inclusive
    return base_ns


def _read_entries(log_path: Path) -> list[LineageEntry]:
    if not log_path.exists():
        return []
    entries: list[LineageEntry] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        entries.append(LineageEntry(**record))
    return entries


def _filter_entries(entries: list[LineageEntry], since: date, until: date) -> list[LineageEntry]:
    lo = _date_to_ns_inclusive(since, end_of_day=False)
    hi = _date_to_ns_inclusive(until, end_of_day=True)
    return [e for e in entries if lo <= e.ts_ns <= hi]


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _readme_text(*, org: str, since: date, until: date, entry_count: int) -> str:
    return (
        f"# Compliance pack — {org}\n\n"
        f"**Period:** {since.isoformat()} → {until.isoformat()}\n"
        f"**Entries in period:** {entry_count}\n\n"
        "This bundle implements the record-keeping obligations of Article 12 of\n"
        "Regulation (EU) 2024/1689 (EU AI Act).\n\n"
        "## Contents\n\n"
        "- `article12-evidence.pdf` — human-readable summary keyed to Article 12 paragraphs.\n"
        "- `article12-evidence.csv` — one row per artefact write event.\n"
        "- `lineage-log.jsonl` — raw lineage log filtered to the period.\n"
        "- `signatures/` — per-entry detached Ed25519 JWS (RFC 7515, RFC 8785 JCS).\n"
        "- `agent-cards/` — A2A v1.0 Agent Cards used to verify the signatures.\n"
        "- `verify-instructions.md` — how to re-verify this bundle independently.\n"
        "- `pack-manifest.json` — SLSA-style provenance for this pack.\n"
        "- `pack-manifest.json.sig` — operator-issued Ed25519 JWS over the manifest.\n"
    )


def _verify_instructions() -> str:
    return (
        "# Verifying this compliance pack\n\n"
        "## Quick path\n\n"
        "```\n"
        "pip install bernstein-verify\n"
        "bernstein-verify pack ./acme-compliance-2026-q2.zip\n"
        "```\n\n"
        "Exit 0 + a one-line PASS summary indicates: every entry in\n"
        "`lineage-log.jsonl` re-canonicalises to a hash whose detached JWS in\n"
        "`signatures/` verifies under the Agent Card in `agent-cards/`, and\n"
        "`pack-manifest.json.sig` verifies against the operator public key.\n\n"
        "## Manual path (no Bernstein install)\n\n"
        "1. Unzip the bundle.\n"
        "2. For every line in `lineage-log.jsonl`, RFC 8785 canonicalise the\n"
        "   JSON, sha256 it -> `entry_hash`.\n"
        "3. Open `signatures/<hex(entry_hash)>.jws`; verify the detached\n"
        "   Ed25519 JWS (RFC 7515 + RFC 7797 `b64=false`) against the public\n"
        "   key in the matching `agent-cards/<agent_id>.json`.\n"
        "4. Verify `pack-manifest.json.sig` against the operator public key\n"
        "   you received out of band.\n"
    )


def _load_operator_signer(key_path: Path) -> str:
    """Return the operator private key PEM contents.

    The compliance pack manifest is short and per-pack, so we re-use the
    lineage Ed25519 JWS primitives directly rather than the heavier
    KMS adapter surface. Customers running with KMS-backed keys can
    point ``operator_key_path`` at a file the adapter writes ephemerally
    (see ``bernstein.core.security.lineage_kms``).
    """
    return key_path.read_text(encoding="utf-8")


def _builder_label() -> str:
    try:
        version = metadata.version("bernstein")
    except metadata.PackageNotFoundError:  # pragma: no cover - dev shim
        version = "0+unknown"
    return f"bernstein/{version} compliance.pack"


def build_pack(
    *,
    since: date,
    until: date,
    org: str,
    lineage_dir: Path,
    agent_cards_dir: Path,
    output_path: Path,
    operator_key_path: Path,
) -> Path:
    """Assemble the Article 12 evidence ZIP.

    Args:
        since: Window start (inclusive, UTC calendar day).
        until: Window end (inclusive, UTC calendar day).
        org: Customer-visible organisation name; surfaces in the PDF/README.
        lineage_dir: Path to ``.sdd/lineage/`` (must contain ``log.jsonl``;
            ``signatures/`` is optional but typical).
        agent_cards_dir: Path to ``.sdd/agents/`` (Agent Card JSON files).
        output_path: Where to write the resulting ``.zip``.
        operator_key_path: PEM PKCS#8 Ed25519 private key used to sign the
            manifest. The matching public key must be handed to the
            auditor out of band.

    Returns:
        ``output_path``.
    """
    build_started_at = datetime.now(UTC).isoformat(timespec="seconds")

    log_path = lineage_dir / "log.jsonl"
    signatures_src = lineage_dir / "signatures"

    all_entries = _read_entries(log_path)
    filtered = _filter_entries(all_entries, since, until)

    # 1. lineage-log.jsonl (filtered)
    log_lines = [json.dumps(asdict(e), sort_keys=True) for e in filtered]
    log_bytes = ("\n".join(log_lines) + ("\n" if log_lines else "")).encode("utf-8")

    # 2. article12-evidence.csv
    csv_bytes = render_csv(filtered).encode("utf-8")

    # 3. article12-evidence.pdf
    pdf_bytes = render_pdf(
        filtered,
        org=org,
        period=(since.isoformat(), until.isoformat()),
    )

    # 4. README.md
    readme_bytes = _readme_text(
        org=org,
        since=since,
        until=until,
        entry_count=len(filtered),
    ).encode("utf-8")

    # 5. verify-instructions.md
    verify_bytes = _verify_instructions().encode("utf-8")

    # 6. signatures/ -- only entries we kept.
    in_window_hashes = {entry_hash(e).split(":", 1)[1] for e in filtered}
    sig_payload: dict[str, bytes] = {}
    if signatures_src.exists():
        for sig_file in signatures_src.iterdir():
            if not sig_file.is_file() or not sig_file.name.endswith(".jws"):
                continue
            stem = sig_file.stem
            if stem in in_window_hashes:
                sig_payload[f"signatures/{sig_file.name}"] = sig_file.read_bytes()

    # 7. agent-cards/ -- only cards referenced by filtered entries.
    used_agent_ids = {e.agent_id for e in filtered}
    card_payload: dict[str, bytes] = {}
    if agent_cards_dir.exists():
        for card_file in agent_cards_dir.iterdir():
            if not card_file.is_file() or not card_file.name.endswith(".json"):
                continue
            try:
                card_data = json.loads(card_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if card_data.get("agent_id") in used_agent_ids or not used_agent_ids:
                card_payload[f"agent-cards/{card_file.name}"] = card_file.read_bytes()

    # 8. pack-manifest.json -- SLSA-style.
    input_hashes: dict[str, str] = {
        "lineage-log.jsonl": _sha256(log_bytes),
        "article12-evidence.csv": _sha256(csv_bytes),
        "article12-evidence.pdf": _sha256(pdf_bytes),
        "README.md": _sha256(readme_bytes),
        "verify-instructions.md": _sha256(verify_bytes),
    }
    for name, content in sorted(sig_payload.items()):
        input_hashes[name] = _sha256(content)
    for name, content in sorted(card_payload.items()):
        input_hashes[name] = _sha256(content)

    # Roll up the per-paragraph facts so the manifest is itself a
    # self-contained evidence statement (auditors can read it without
    # parsing the PDF).
    period_strs = (since.isoformat(), until.isoformat())
    article12_facts: list[dict[str, Any]] = [fn(filtered, period_strs) for fn in ARTICLE12_PARAGRAPH_MAP.values()]

    build_finished_at = datetime.now(UTC).isoformat(timespec="seconds")

    manifest: dict[str, Any] = {
        "schema": "https://bernstein.run/compliance/pack-manifest/v1",
        "builder": _builder_label(),
        "org": org,
        "period": {"since": since.isoformat(), "until": until.isoformat()},
        "build_started_at": build_started_at,
        "build_finished_at": build_finished_at,
        "input_hashes": input_hashes,
        "entry_count": len(filtered),
        "article12_facts": article12_facts,
        "operator_kid": _OPERATOR_KID,
    }
    # Compute output_hash over the canonical manifest body itself so
    # the manifest is self-anchoring: a verifier can derive output_hash
    # from the bytes they're holding.
    manifest_bytes_no_output = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["output_hash"] = _sha256(manifest_bytes_no_output)
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # 9. pack-manifest.json.sig — operator-signed.
    operator_pem = _load_operator_signer(operator_key_path)
    sig = sign_detached(manifest_bytes, operator_pem, kid=_OPERATOR_KID)

    # 10. Assemble ZIP. Deterministic ordering for reproducible packs.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", readme_bytes)
        zf.writestr("article12-evidence.pdf", pdf_bytes)
        zf.writestr("article12-evidence.csv", csv_bytes)
        zf.writestr("lineage-log.jsonl", log_bytes)
        zf.writestr("verify-instructions.md", verify_bytes)
        for name in sorted(sig_payload):
            zf.writestr(name, sig_payload[name])
        for name in sorted(card_payload):
            zf.writestr(name, card_payload[name])
        zf.writestr("pack-manifest.json", manifest_bytes)
        zf.writestr("pack-manifest.json.sig", sig)

    return output_path
