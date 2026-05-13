"""Shared helpers for deterministic lineage demo fixture generation.

Each demo script seeds a `random.Random` and uses fixed UTC timestamps so the
fixture log + signatures + Agent Cards round-trip byte-for-byte across runs.

We intentionally only import from `bernstein.core.lineage.{entry,identity}`
plus stdlib + `cryptography` so the demo generator stays decoupled from the
parallel work on recorder / store / pack / verify CLIs.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

if TYPE_CHECKING:
    import random

from bernstein.core.lineage.entry import (
    LINEAGE_ENTRY_VERSION,
    LineageEntry,
    canonicalise,
    entry_hash,
)
from bernstein.core.lineage.identity import AgentCard, sign_detached

# Static operator HMAC secret for fixtures. Real deployments use a KMS-managed
# key; fixtures keep it constant so the wire bytes are reproducible.
DEMO_HMAC_SECRET = b"bernstein-lineage-demo-hmac-secret-v1"


def deterministic_keypair(rng: random.Random) -> tuple[str, str, bytes]:
    """Generate an Ed25519 keypair from a seeded RNG.

    Returns (private_pem, public_pem, raw_seed). We sample 32 bytes from the
    seeded `random.Random` and feed those into `Ed25519PrivateKey.from_private_bytes`
    so the same script seed always produces the same identity.
    """
    seed = bytes(rng.randrange(0, 256) for _ in range(32))
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return priv_pem, pub_pem, seed


def fixed_iso_to_ns(iso: str) -> int:
    """Convert a fixed ISO-8601 UTC timestamp string to ns since epoch."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _hmac_hex(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def build_entry(
    *,
    artefact_path: str,
    artefact_kind: str,
    content: bytes,
    parent_hashes: list[str],
    agent_id: str,
    agent_card_kid: str,
    tool_call_id: str,
    span_id: str,
    ts_ns: int,
) -> LineageEntry:
    """Build a fully-validated LineageEntry with HMAC envelope.

    The operator HMAC is computed over the canonical body MINUS the
    operator_hmac field itself (so the HMAC commits to the entry but isn't
    self-referential). We do this by building a dict, canonicalising it
    without the HMAC, then putting the HMAC in.
    """
    body_for_hmac = {
        "v": LINEAGE_ENTRY_VERSION,
        "artefact_path": artefact_path,
        "artefact_kind": artefact_kind,
        "content_hash": sha256_hex(content),
        "parent_hashes": parent_hashes,
        "agent_id": agent_id,
        "agent_card_kid": agent_card_kid,
        "tool_call_id": tool_call_id,
        "span_id": span_id,
        "ts_ns": ts_ns,
    }
    canonical_for_hmac = json.dumps(
        body_for_hmac,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    op_hmac = _hmac_hex(DEMO_HMAC_SECRET, canonical_for_hmac)
    return LineageEntry(
        v=LINEAGE_ENTRY_VERSION,
        artefact_path=artefact_path,
        artefact_kind=artefact_kind,
        content_hash=sha256_hex(content),
        parent_hashes=parent_hashes,
        agent_id=agent_id,
        agent_card_kid=agent_card_kid,
        tool_call_id=tool_call_id,
        span_id=span_id,
        ts_ns=ts_ns,
        operator_hmac=op_hmac,
    )


def write_agent_card(out_dir: Path, agent_id: str, kid: str, public_key_pem: str) -> Path:
    """Write a minimal A2A v1.0 Agent Card JSON file. Returns path."""
    card_obj = {
        "protocolVersion": "a2a/1.0",
        "name": agent_id,
        "url": f"local://agents/{agent_id}",
        "capabilities": ["lineage.sign", "lineage.record"],
        "signatures": [],
        "keys": [
            {
                "kid": kid,
                "kty": "OKP",
                "crv": "Ed25519",
                "alg": "EdDSA",
                "use": "sig",
                "pem": public_key_pem,
            }
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    # Filenames replace ":" with "_" so the fixtures check out on Windows
    # (NTFS rejects ":" in paths). The canonical agent_id inside the card
    # body retains the original colon — only the on-disk filename is changed.
    safe_id = agent_id.replace(":", "_")
    p = out_dir / f"{safe_id}.json"
    p.write_text(
        json.dumps(card_obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return p


def write_entry_with_signature(
    log_path: Path,
    sigs_dir: Path,
    entry: LineageEntry,
    private_key_pem: str,
    kid: str,
) -> str:
    """Append an entry to log.jsonl and write its detached JWS sidecar.

    Returns the entry hash.
    """
    canonical = canonicalise(entry)
    eh = entry_hash(entry)
    jws = sign_detached(canonical, private_key_pem, kid=kid)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    sigs_dir.mkdir(parents=True, exist_ok=True)

    # Match the storage layout from ADR-009 §4: signatures/<aa>/<full>/<entry_hash>.jws
    artefact_path_hash = hashlib.sha256(entry.artefact_path.encode("utf-8")).hexdigest()
    shard = artefact_path_hash[:2]
    artefact_dir = sigs_dir / shard / artefact_path_hash
    artefact_dir.mkdir(parents=True, exist_ok=True)

    eh_filename_safe = eh.replace("sha256:", "sha256_")
    (artefact_dir / f"{eh_filename_safe}.jws").write_text(jws + "\n", encoding="utf-8")

    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True) + "\n")

    return eh


def reset_log(log_path: Path) -> None:
    """Truncate the demo log file so reruns are deterministic."""
    if log_path.exists():
        log_path.unlink()
    log_path.parent.mkdir(parents=True, exist_ok=True)


def reset_signatures(sigs_dir: Path) -> None:
    """Wipe the signatures dir so old runs don't leave dangling .jws files."""
    if sigs_dir.exists():
        for root, _dirs, files in os.walk(sigs_dir, topdown=False):
            for name in files:
                Path(root, name).unlink()
            with contextlib.suppress(OSError):
                Path(root).rmdir()
    sigs_dir.mkdir(parents=True, exist_ok=True)


def make_agent(
    rng: random.Random,
    *,
    agent_id: str,
    card_dir: Path,
    kid_suffix: str = "001",
) -> tuple[str, AgentCard]:
    """Create a deterministic agent: keypair + AgentCard + on-disk card file.

    Returns (private_key_pem, agent_card).
    """
    priv_pem, pub_pem, _seed = deterministic_keypair(rng)
    kid = f"key-2026-{kid_suffix}"
    write_agent_card(card_dir, agent_id, kid, pub_pem)
    return priv_pem, AgentCard(agent_id=agent_id, kid=kid, public_key_pem=pub_pem)
