"""Lineage v1 — Sigstore-style per-artefact transparency log.

See docs/decisions/009-lineage-v1.md for the design rationale.

Public API:

  - LineageEntry — frozen dataclass for a single write event
  - canonicalise, entry_hash — RFC 8785 JCS bytes + sha256 digest
  - AgentCard — minimal A2A v1.0 Agent Card subset
  - generate_keypair, sign_detached, verify_detached — Ed25519 JWS RFC 7515

Storage (LineageStore), recorder (LineageRecorder), gate, merge, compliance
pack, and MCP resource live in sibling modules under this package and re-export
through here once the corresponding feature branches land.
"""

from bernstein.core.lineage.entry import (
    ARTEFACT_KINDS,
    LINEAGE_ENTRY_VERSION,
    LineageEntry,
    canonicalise,
    entry_hash,
)
from bernstein.core.lineage.identity import (
    AgentCard,
    generate_keypair,
    sign_detached,
    verify_detached,
)

__all__ = [
    "ARTEFACT_KINDS",
    "LINEAGE_ENTRY_VERSION",
    "AgentCard",
    "LineageEntry",
    "canonicalise",
    "entry_hash",
    "generate_keypair",
    "sign_detached",
    "verify_detached",
]
