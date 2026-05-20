"""Lineage chain interop: carry a signed Bernstein chain through A2A.

When a Bernstein run delegates work to a peer agent over A2A, the run's
signed lineage chain travels inside the A2A evidence envelope under the
``bernstein.lineage_v2`` field. The receiving side appends the delegated
work to its *own* chain with a cross-org boundary marker so an auditor can
see exactly where one organisation's chain hands off to another.

This module reuses the existing HMAC chain in
:mod:`bernstein.core.lineage.tracker_audit`: the envelope payload is the
canonical bytes of the source chain's tracker-audit entries plus a chain
digest, and the receiving side records the handoff as a normal signed entry
(``action="comment"``) whose body is the cross-org boundary marker. No new
signing primitive is introduced -- the boundary entry is verifiable by the
receiver's existing ``bernstein lineage tracker-audit verify``.

Envelope shape (the value of ``bernstein.lineage_v2``)::

    {
      "schema_version": 1,
      "source_issuer": "<issuer id from the sender's capability card>",
      "chain_digest": "sha256:...",        # over the canonical entry bytes
      "entries": [ <tracker-audit entry dict>, ... ]
    }

The ``chain_digest`` lets the receiver bind the boundary entry it appends to
the exact source chain it received: the digest is recorded in the boundary
marker, so tampering with the carried chain after the fact is detectable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bernstein.core.lineage.tracker_audit import (
    TrackerActor,
    entry_from_payload,
    entry_to_body,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bernstein.core.lineage.tracker_audit import (
        TrackerAuditEntry,
        TrackerAuditLog,
    )

__all__ = [
    "CROSS_ORG_BOUNDARY_MARKER",
    "LINEAGE_ENVELOPE_FIELD",
    "LINEAGE_ENVELOPE_SCHEMA_VERSION",
    "LineageEnvelope",
    "append_cross_org_segment",
    "chain_digest",
    "wrap_lineage_chain",
]

#: A2A envelope field that carries the signed Bernstein lineage chain.
LINEAGE_ENVELOPE_FIELD: str = "bernstein.lineage_v2"

#: Marker recorded in the boundary entry's tracker name so the cross-org
#: handoff is greppable in an audit export.
CROSS_ORG_BOUNDARY_MARKER: str = "a2a-cross-org-boundary"

#: Envelope schema version. Bumping requires a parallel reader.
LINEAGE_ENVELOPE_SCHEMA_VERSION: int = 1


def _canonical(payload: dict[str, Any]) -> bytes:
    """Return stable JCS-style bytes for ``payload``."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def chain_digest(entries: Sequence[TrackerAuditEntry]) -> str:
    """Return a ``sha256:`` digest over the canonical bytes of ``entries``.

    The digest is computed over the newline-joined JCS canonicalisation of
    each entry body, in order, so it binds both the entry contents and their
    sequence. Two chains with the same entries in a different order produce
    different digests.
    """
    hasher = hashlib.sha256()
    for entry in entries:
        hasher.update(_canonical(entry_to_body(entry)))
        hasher.update(b"\n")
    return "sha256:" + hasher.hexdigest()


@dataclass(frozen=True)
class LineageEnvelope:
    """The ``bernstein.lineage_v2`` payload carried in an A2A envelope.

    Attributes:
        source_issuer: Issuer id from the sender's capability card.
        chain_digest: ``sha256:`` digest over the carried entries.
        entries: The source chain's tracker-audit entries.
        schema_version: Envelope schema version.
    """

    source_issuer: str
    chain_digest: str
    entries: list[TrackerAuditEntry] = field(default_factory=list)
    schema_version: int = LINEAGE_ENVELOPE_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-compatible envelope payload."""
        return {
            "schema_version": self.schema_version,
            "source_issuer": self.source_issuer,
            "chain_digest": self.chain_digest,
            "entries": [entry_to_body(entry) for entry in self.entries],
        }

    def to_envelope_field(self) -> dict[str, dict[str, Any]]:
        """Return ``{LINEAGE_ENVELOPE_FIELD: payload}`` for splicing in."""
        return {LINEAGE_ENVELOPE_FIELD: self.to_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LineageEnvelope:
        """Rebuild an envelope from a parsed payload.

        Raises:
            ValueError: If required keys are missing or malformed.
        """
        if not isinstance(payload, dict):
            raise ValueError("lineage envelope payload must be an object")
        source_issuer = payload.get("source_issuer")
        digest = payload.get("chain_digest")
        raw_entries = payload.get("entries")
        if not isinstance(source_issuer, str) or not source_issuer:
            raise ValueError("lineage envelope missing 'source_issuer'")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ValueError("lineage envelope missing valid 'chain_digest'")
        if not isinstance(raw_entries, list):
            raise ValueError("lineage envelope 'entries' must be a list")
        entries = [entry_from_payload(item) for item in raw_entries]
        recomputed = chain_digest(entries)
        if recomputed != digest:
            raise ValueError(f"lineage envelope chain_digest mismatch (carried {digest}, recomputed {recomputed})")
        return cls(
            source_issuer=source_issuer,
            chain_digest=digest,
            entries=entries,
            schema_version=int(payload.get("schema_version", LINEAGE_ENVELOPE_SCHEMA_VERSION)),
        )

    @classmethod
    def from_envelope_field(cls, envelope: dict[str, Any]) -> LineageEnvelope:
        """Extract and rebuild the envelope from a full A2A envelope dict.

        Raises:
            ValueError: If the ``bernstein.lineage_v2`` field is absent.
        """
        payload = envelope.get(LINEAGE_ENVELOPE_FIELD)
        if not isinstance(payload, dict):
            raise ValueError(f"A2A envelope missing '{LINEAGE_ENVELOPE_FIELD}' field")
        return cls.from_payload(payload)


def wrap_lineage_chain(
    log: TrackerAuditLog,
    *,
    source_issuer: str,
    tracker_name: str | None = None,
    ticket_id: str | None = None,
) -> LineageEnvelope:
    """Read a signed chain from ``log`` and wrap it in an A2A envelope.

    Args:
        log: The source :class:`TrackerAuditLog` to read entries from.
        source_issuer: Issuer id from the sender's capability card; recorded
            so the receiver can attribute the carried chain.
        tracker_name: Optional filter to wrap only one tracker's entries.
        ticket_id: Optional filter to wrap only one ticket's entries.

    Returns:
        A :class:`LineageEnvelope` ready to splice into an A2A payload via
        :meth:`LineageEnvelope.to_envelope_field`.
    """
    if tracker_name is not None or ticket_id is not None:
        entries = log.filter(tracker_name=tracker_name, ticket_id=ticket_id)
    else:
        entries = log.read()
    return LineageEnvelope(
        source_issuer=source_issuer,
        chain_digest=chain_digest(entries),
        entries=list(entries),
    )


def append_cross_org_segment(
    receiver_log: TrackerAuditLog,
    envelope: LineageEnvelope,
    *,
    actor: TrackerActor,
    ticket_id: str,
    lifecycle_event_id: str | None = None,
) -> TrackerAuditEntry:
    """Append a cross-org boundary marker to the receiver's own chain.

    The receiving side records the handoff as a normal signed tracker-audit
    entry whose body captures the source issuer and the carried chain's
    digest. The entry's ``tracker_name`` is :data:`CROSS_ORG_BOUNDARY_MARKER`
    so the boundary is greppable, and its ``output_blob`` is the canonical
    envelope payload so the receiver's chain binds the exact bytes received.
    The entry is signed and chained by the receiver's existing
    :class:`TrackerAuditLog`, so it verifies under the receiver's operator
    HMAC key with no new primitive.

    Args:
        receiver_log: The receiver's :class:`TrackerAuditLog`.
        envelope: The lineage envelope extracted from the A2A payload.
        actor: The receiving actor (session/role/model) recording the entry.
        ticket_id: The receiver-side ticket the delegated work attaches to.
        lifecycle_event_id: Optional lifecycle correlation id.

    Returns:
        The appended, signed :class:`TrackerAuditEntry` boundary marker.
    """
    marker_body = _canonical(
        {
            "marker": CROSS_ORG_BOUNDARY_MARKER,
            "source_issuer": envelope.source_issuer,
            "source_chain_digest": envelope.chain_digest,
            "source_entry_count": len(envelope.entries),
        }
    )

    payload_bytes = _canonical(envelope.to_payload())

    result = receiver_log.append(
        tracker_name=CROSS_ORG_BOUNDARY_MARKER,
        ticket_id=ticket_id,
        action="comment",
        actor=actor,
        input_prompt=marker_body,
        output_blob=payload_bytes,
        lifecycle_event_id=lifecycle_event_id,
    )
    return result.entry
