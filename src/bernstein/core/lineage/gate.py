"""Lineage CI gate (ADR-009 §6.2).

`check(log_path, agent_cards_dir)` returns a `GateResult` reporting whether
every entry in `log.jsonl` is:

  1. Parsable as JSON and satisfies the LineageEntry schema.
  2. Backed by a matching detached JWS sidecar that verifies against the
     agent's published Agent Card (Ed25519, RFC 7515 detached).
  3. (Optional) HMAC-protected with the supplied operator secret.
  4. Anchored — every `parent_hash` resolves to another entry in the log.
  5. Free of unresolved forks (each open tip is single OR is a merge entry).
  6. (Optional) Authored by a steward-allow-listed agent when the entry is
     a merge (parent_hashes length >= 2).

The check is read-only and does not depend on the LineageStore — it can
operate on a frozen log + cards directory (e.g. an audit pack).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bernstein.core.lineage.entry import LineageEntry, canonicalise, entry_hash
from bernstein.core.lineage.identity import AgentCard, verify_detached
from bernstein.core.lineage.tips import compute_tips, detect_forks

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class GateResult:
    """Outcome of `check`. `ok` is True iff `failures` is empty."""

    ok: bool
    failures: list[str] = field(default_factory=list)


def _load_cards(cards_dir: Path) -> dict[str, AgentCard]:
    """Load all Agent Cards under cards_dir/<agent-id>/card.json."""
    out: dict[str, AgentCard] = {}
    if not cards_dir.exists():
        return out
    for card_file in cards_dir.glob("*/card.json"):
        try:
            data = json.loads(card_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id")
        kid = data.get("kid")
        pub = data.get("public_key_pem")
        if not (isinstance(agent_id, str) and isinstance(kid, str) and isinstance(pub, str)):
            continue
        out[agent_id] = AgentCard(
            agent_id=agent_id,
            kid=kid,
            public_key_pem=pub,
            protocol_version=data.get("protocolVersion", "a2a/1.0"),
        )
    return out


def _shard_path(artefact_path: str) -> tuple[str, str]:
    """Returns (shard, full_hash) for the per-artefact signatures layout."""
    digest = hashlib.sha256(artefact_path.encode()).hexdigest()
    return digest[:2], digest


def _signature_path(log_dir: Path, entry: LineageEntry, eh: str) -> Path:
    shard, full = _shard_path(entry.artefact_path)
    return log_dir / "signatures" / shard / full / (eh.replace("sha256:", "") + ".jws")


def _parse_log(log_path: Path) -> tuple[list[LineageEntry], list[str]]:
    """Parse the JSONL log; return (entries, parse_failures)."""
    entries: list[LineageEntry] = []
    failures: list[str] = []
    if not log_path.exists():
        return entries, failures
    with log_path.open() as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                failures.append(f"log line {line_no}: parse error: {exc}")
                continue
            try:
                entry = LineageEntry(**obj)
            except (TypeError, ValueError) as exc:
                failures.append(f"log line {line_no}: corrupt entry: {exc}")
                continue
            entries.append(entry)
    return entries, failures


def check(
    log_path: Path,
    agent_cards_dir: Path,
    *,
    operator_secret: bytes | None = None,
    steward_allowlist: frozenset[str] | None = None,
) -> GateResult:
    """Run all lineage invariants against the log + cards on disk.

    Args:
        log_path: path to `.sdd/lineage/log.jsonl`.
        agent_cards_dir: directory containing `<agent-id>/card.json`.
        operator_secret: when given, verify each entry's `operator_hmac`
            against an HMAC of the entry's canonical bytes (without the
            HMAC field itself). When None, the HMAC check is skipped.
        steward_allowlist: when given, every merge entry's `agent_id` must
            be in this set or the gate fails (privilege escalation guard).

    Returns:
        GateResult with ok=True iff failures is empty.
    """
    failures: list[str] = []
    entries, parse_fails = _parse_log(log_path)
    failures.extend(parse_fails)

    if not entries:
        return GateResult(ok=not failures, failures=failures)

    cards = _load_cards(agent_cards_dir)
    log_dir = log_path.parent

    # Per-entry signature + HMAC + card lookups.
    known_hashes: set[str] = set()
    for entry in entries:
        eh = entry_hash(entry)
        known_hashes.add(eh)
        card = cards.get(entry.agent_id)
        if card is None:
            failures.append(f"{entry.artefact_path}: unknown agent card for {entry.agent_id!r} (entry {eh})")
            continue
        # Signature
        sig_path = _signature_path(log_dir, entry, eh)
        if not sig_path.exists():
            failures.append(f"{entry.artefact_path}: missing signature sidecar for entry {eh}")
        else:
            try:
                jws = sig_path.read_text().strip()
            except OSError as exc:
                failures.append(f"{entry.artefact_path}: cannot read signature {sig_path}: {exc}")
                continue
            canonical = canonicalise(entry)
            if not verify_detached(canonical, jws, card):
                failures.append(f"{entry.artefact_path}: invalid signature on entry {eh}")
        # HMAC
        if operator_secret is not None:
            body = json.dumps(
                {
                    "p": entry.parent_hashes,
                    "h": entry.content_hash,
                    "ts": entry.ts_ns,
                }
            ).encode()
            expected = _hmac.new(operator_secret, body, hashlib.sha256).hexdigest()
            if not _hmac.compare_digest(expected, entry.operator_hmac):
                failures.append(f"{entry.artefact_path}: HMAC mismatch on entry {eh}")
        # Steward allow-list for merge entries.
        if steward_allowlist is not None and len(entry.parent_hashes) >= 2 and entry.agent_id not in steward_allowlist:
            failures.append(
                f"{entry.artefact_path}: merge entry {eh} written by non-steward {entry.agent_id!r} (not in allowlist)"
            )

    # Parent-hash chain integrity.
    for entry in entries:
        for ph in entry.parent_hashes:
            if ph not in known_hashes:
                failures.append(f"{entry.artefact_path}: dangling parent_hash {ph} on entry {entry_hash(entry)}")

    # Tip / fork analysis.
    tips = compute_tips(entries)
    for path, tipset in tips.items():
        if len(tipset["open"]) > 1:
            failures.append(f"{path}: {len(tipset['open'])} unresolved open tips: {tipset['open']}")
    for fork in detect_forks(entries):
        # A fork is "resolved" iff some entry has parent_hashes covering ALL
        # of the fork's child_hashes (subset thereof — diamond merges count).
        resolved = False
        children = set(fork.child_hashes)
        for entry in entries:
            if len(entry.parent_hashes) >= 2 and children.issubset(set(entry.parent_hashes)):
                resolved = True
                break
        if not resolved:
            failures.append(
                f"{fork.artefact_path}: unresolved fork at parent {fork.parent_hash} "
                f"with children {list(fork.child_hashes)}"
            )

    return GateResult(ok=not failures, failures=failures)


__all__ = ["GateResult", "check"]
