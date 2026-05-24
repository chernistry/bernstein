"""Image-attachment passthrough with provenance (issue #1797).

This module wires the operator-supplied ``--attach <path>`` flow into:

* :mod:`bernstein.core.agents.multimodal` -- the existing
  :class:`MultiModalContext` and ``encode_input`` helpers stay the
  source of truth for base64 encoding and modality detection.
* :mod:`bernstein.core.persistence.cas_store` -- raw attachment bytes
  are stored once by SHA-256, so duplicate attachments dedupe and a
  replay path retrieves the exact bytes that the model API saw.
* :mod:`bernstein.core.security.audit_chain` -- every attach call
  records an HMAC-chained ``multimodal.attach`` event carrying the
  bytes' SHA-256, MIME, worker identity, turn sequence, worktree id,
  the operator install signature, and the prior chain digest.
* :mod:`bernstein.core.persistence.lineage_signer` -- the worker's
  lineage v1 receipt is augmented with attachment digests in its
  ``parents`` list via :func:`worker_lineage_parents`.

The :func:`refuse_when_incapable` helper performs capability gating
BEFORE any process is launched: if the selected adapter does not
report ``is_multimodal_capable() == True`` and at least one
attachment is present, a :class:`CapabilityRefusal` is raised whose
``suggested_adapters`` field names adapters that do support
attachments. The orchestrator surfaces this as a structured error
rather than a stack trace.

Worktree pinning
----------------
An attachment is stored in CAS at SHA-256 time but only resolves back
to bytes for workers in the same worktree it was attached from. The
worktree id is embedded in the ``multimodal.attach`` event payload;
:func:`resolve_attachment_for_worker` consults the chain on lookup
and raises :class:`WorktreeAccessDenied` for any cross-worktree
attempt.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path  # runtime use in encode_one
from typing import TYPE_CHECKING

from bernstein.core.agents.multimodal import (
    MultiModalContext,
    build_multimodal_context,
    encode_input,
    is_multimodal_capable,
)
from bernstein.core.identity.install_rev import get_install_rev
from bernstein.core.persistence.lineage_signer import (
    build_attachment_parent_uri,
)
from bernstein.core.security.audit_chain import (
    EVENT_MULTIMODAL_ATTACH,
    record_multimodal_attach,
)

if TYPE_CHECKING:
    from bernstein.core.persistence.cas_store import CASStore
    from bernstein.core.security.audit_chain import AuditChainStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Capability gating
# ---------------------------------------------------------------------------


#: Adapters that advertise ``is_multimodal_capable() == True``. Kept
#: as a sorted tuple so error messages are deterministic.
_CAPABLE_SUGGESTIONS: tuple[str, ...] = ("claude", "gemini")


class CapabilityRefusal(RuntimeError):
    """Raised when an incapable adapter is asked to consume attachments.

    Attributes:
        adapter_name: The adapter that was asked.
        suggested_adapters: Adapter names that DO support attachments.
    """

    def __init__(self, adapter_name: str, suggested_adapters: tuple[str, ...]) -> None:
        self.adapter_name = adapter_name
        self.suggested_adapters = suggested_adapters
        super().__init__(
            f"Adapter {adapter_name!r} does not support multimodal attachments. "
            f"Suggested capable adapters: {', '.join(suggested_adapters)}."
        )


def refuse_when_incapable(
    *,
    adapter_name: str,
    attachments: list[str] | tuple[str, ...],
) -> None:
    """Raise :class:`CapabilityRefusal` for incapable + non-empty combos.

    Args:
        adapter_name: Registry name of the selected adapter
            (case-insensitive).
        attachments: Iterable of operator-supplied attachment paths.

    Raises:
        CapabilityRefusal: When the adapter is not multimodal-capable
            and at least one attachment is present.
    """
    if not attachments:
        return
    if is_multimodal_capable(adapter_name):
        return
    raise CapabilityRefusal(adapter_name=adapter_name, suggested_adapters=_CAPABLE_SUGGESTIONS)


# ---------------------------------------------------------------------------
# AttachmentResolution dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentResolution:
    """A single attachment, resolved at spawn time.

    Attributes:
        sha256: Hex digest of the attachment bytes.
        mime: MIME type as resolved by :func:`encode_input`.
        worktree_id: The worktree this attachment is pinned to.
        source_path: Original on-disk path (for diagnostics).
    """

    sha256: str
    mime: str
    worktree_id: str
    source_path: str


@dataclass(frozen=True)
class AttachmentBuildResult:
    """Aggregate result returned by :func:`build_attachment_context`.

    Attributes:
        context: The :class:`MultiModalContext` to pass to the adapter.
        resolutions: Per-attachment provenance records in input order.
    """

    context: MultiModalContext
    resolutions: tuple[AttachmentResolution, ...]


# ---------------------------------------------------------------------------
# Operator install identity signature
# ---------------------------------------------------------------------------


def _operator_install_id_sig() -> str:
    """Return a stable per-install identity signature for the audit record.

    The signature is derived from the same install-rev token surfaced by
    :func:`bernstein.core.identity.install_rev.get_install_rev`. The
    returned value is a hex SHA-256 over the install rev so that a raw
    install token never appears in plain text inside the audit chain.
    """
    rev = get_install_rev()
    return hashlib.sha256(rev.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Build context at spawn time
# ---------------------------------------------------------------------------


def build_attachment_context(
    *,
    attachments: list[str] | tuple[str, ...],
    worker_id: str,
    turn_seq: int,
    worktree_id: str,
    cas: CASStore,
    audit_chain: AuditChainStore,
) -> AttachmentBuildResult:
    """Read attachments from disk, store bytes in CAS, and record events.

    For each ``attachments`` entry the helper:

    1. Encodes the file into a :class:`MultiModalInput` via
       :func:`encode_input` (the same code path the adapters use).
    2. Computes the SHA-256 of the *raw* file bytes and stores them in
       *cas* so a replay path can fetch the exact bytes that were sent
       to the model API.
    3. Appends a ``multimodal.attach`` event to *audit_chain* carrying
       the SHA-256, MIME type, operator install signature, worker id,
       turn sequence, worktree id, and the previous chain digest.

    Args:
        attachments: Operator-supplied attachment paths.
        worker_id: Id of the worker that will consume the attachment.
        turn_seq: Monotonic turn sequence number for the worker.
        worktree_id: Worktree the worker runs in.
        cas: Content-addressed blob store (reused, not re-created).
        audit_chain: Audit chain store.

    Returns:
        An :class:`AttachmentBuildResult` carrying the multimodal
        context (ready to pass to an adapter) and the per-attachment
        provenance records.
    """
    if not attachments:
        return AttachmentBuildResult(
            context=build_multimodal_context([]),
            resolutions=(),
        )

    paths: list[str | Path] = list(attachments)
    context = build_multimodal_context(paths)

    resolutions: list[AttachmentResolution] = []
    operator_sig = _operator_install_id_sig()
    for inp in context.inputs:
        if inp.content_path is None or not inp.content_base64:
            # build_multimodal_context skipped a missing file or could
            # not produce a base64 payload; nothing to anchor in CAS /
            # the chain. Skip provenance recording so downstream
            # callers see no resolution for it.
            continue
        # Hash the bytes that will actually travel to the model API,
        # not a separate re-read of the source file. The base64
        # payload in ``content_base64`` IS what the adapter inlines in
        # the request body; decoding it here gives us the identical
        # bytes for CAS + the audit-chain digest, eliminating the race
        # where the on-disk file changes between encode time and
        # attest time. (bot-ack: 3284182756 -- CodeRabbit critical.)
        try:
            raw_bytes = base64.b64decode(inp.content_base64, validate=True)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Skipping attachment %s: invalid base64 payload (%s)",
                inp.content_path,
                exc,
            )
            continue
        digest = hashlib.sha256(raw_bytes).hexdigest()
        cas.put(
            raw_bytes,
            content_type=inp.mime_type,
            metadata={
                "source_path": str(inp.content_path),
                "worktree_id": worktree_id,
                "worker_id": worker_id,
            },
        )
        record_multimodal_attach(
            chain=audit_chain,
            sha256=digest,
            mime=inp.mime_type,
            operator_install_id_sig=operator_sig,
            worker_id=worker_id,
            turn_seq=turn_seq,
            worktree_id=worktree_id,
        )
        resolutions.append(
            AttachmentResolution(
                sha256=digest,
                mime=inp.mime_type,
                worktree_id=worktree_id,
                source_path=str(inp.content_path),
            )
        )

    return AttachmentBuildResult(context=context, resolutions=tuple(resolutions))


# ---------------------------------------------------------------------------
# Encode a single file (test convenience)
# ---------------------------------------------------------------------------


def encode_one(file_path: str | Path) -> tuple[str, str, str]:
    """Encode a single attachment for adapter consumption.

    Returns ``(base64_content, mime_type, sha256_digest)``. The digest is
    over the raw file bytes -- so it matches what
    :func:`build_attachment_context` records in the audit chain.
    """
    inp = encode_input(file_path)
    raw = Path(file_path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return (inp.content_base64 or "", inp.mime_type, digest)


# ---------------------------------------------------------------------------
# Worktree-pinned resolver
# ---------------------------------------------------------------------------


class WorktreeAccessDenied(RuntimeError):
    """Raised when a worker in worktree B requests an attachment from A."""

    def __init__(self, sha256: str, attached_worktree: str, requesting_worktree: str) -> None:
        self.sha256 = sha256
        self.attached_worktree = attached_worktree
        self.requesting_worktree = requesting_worktree
        super().__init__(
            f"Attachment {sha256[:12]}... was attached in worktree "
            f"{attached_worktree!r} but worker in worktree "
            f"{requesting_worktree!r} attempted to resolve it. Cross-worktree "
            "access is denied."
        )


def resolve_attachment_for_worker(
    *,
    sha256: str,
    requesting_worktree_id: str,
    cas: CASStore,
    audit_chain: AuditChainStore,
) -> bytes:
    """Return attached bytes if the requesting worktree owns the attach.

    Looks up the most recent ``multimodal.attach`` event matching
    ``sha256``. If that event's ``worktree_id`` matches
    ``requesting_worktree_id`` the bytes are returned from CAS;
    otherwise :class:`WorktreeAccessDenied` is raised.

    Args:
        sha256: Hex digest of the requested attachment.
        requesting_worktree_id: The worker's worktree id.
        cas: Content-addressed blob store.
        audit_chain: Audit chain to consult for the attach event.

    Returns:
        The raw attachment bytes.

    Raises:
        WorktreeAccessDenied: The attach event's worktree id differs
            from the requesting worktree id.
        FileNotFoundError: No attach event exists for the SHA, or the
            CAS lookup misses.
    """
    entries = audit_chain.query(event_type=EVENT_MULTIMODAL_ATTACH)
    matches = [e for e in entries if e.details.get("sha256") == sha256]
    if not matches:
        raise FileNotFoundError(f"No multimodal.attach event for {sha256[:12]}...")
    # Resolve by (sha256, worktree_id) so concurrent attaches in
    # different worktrees of the same bytes do not poison each other.
    # If any historical attach in the requesting worktree exists for
    # this digest, allow the resolve; otherwise refuse. The list of
    # attaching worktrees (for the structured error) is built from
    # every historical event so the operator sees the full picture.
    # (bot-ack: 3284182761 -- CodeRabbit major.)
    in_worktree = [e for e in matches if str(e.details.get("worktree_id", "")) == requesting_worktree_id]
    if not in_worktree:
        seen_worktrees = sorted(
            {str(e.details.get("worktree_id", "")) for e in matches if e.details.get("worktree_id")}
        )
        raise WorktreeAccessDenied(
            sha256=sha256,
            attached_worktree=", ".join(seen_worktrees) or "<unknown>",
            requesting_worktree=requesting_worktree_id,
        )
    blob = cas.get(sha256)
    if blob is None:
        raise FileNotFoundError(f"CAS miss for {sha256[:12]}...")
    return blob


# ---------------------------------------------------------------------------
# Lineage parents
# ---------------------------------------------------------------------------


def worker_lineage_parents(result: AttachmentBuildResult) -> list[str]:
    """Return canonical lineage parent URIs for *result*'s attachments.

    Empty when no attachments were resolved.
    """
    return [build_attachment_parent_uri(r.sha256) for r in result.resolutions]


__all__ = [
    "AttachmentBuildResult",
    "AttachmentResolution",
    "CapabilityRefusal",
    "WorktreeAccessDenied",
    "build_attachment_context",
    "encode_one",
    "refuse_when_incapable",
    "resolve_attachment_for_worker",
    "worker_lineage_parents",
]
