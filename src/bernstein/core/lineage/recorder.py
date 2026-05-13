"""``LineageRecorder`` — orchestrates a single artefact write into the log.

The recorder is the small piece of glue between the storage layer
(``LineageStore``) and the policy that decides what an entry actually
*means*. It:

1. Computes ``content_hash = sha256(new_content)``.
2. Looks up the current tip(s) for the artefact via the store.
3. Builds a ``LineageEntry`` with the appropriate ``parent_hashes``:
     * empty list → genesis (first write).
     * single parent → linear successor.
     * The caller is responsible for explicit merges; we never invent
       multi-parent entries on the agent's behalf.
4. Computes the HMAC envelope with the operator key over the entry's
   canonical bytes minus the ``operator_hmac`` field itself.
5. Signs ``entry_hash`` (RFC 7515 + RFC 8037 detached JWS, EdDSA) with
   the agent's Ed25519 private key.
6. Hands everything to the store, which fsyncs + flocks the log.
7. Emits an OpenTelemetry span (no-op when telemetry is not initialised).

The recorder rejects path traversal and absolute paths so an attacker
controlling the call site cannot smuggle ``../`` outside the repo or
anchor an artefact at ``/etc/passwd`` — both surface as ``ValueError``
before any HMAC or signature is computed.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import time
from typing import TYPE_CHECKING

from bernstein.core.lineage.entry import LineageEntry, canonicalise, entry_hash
from bernstein.core.lineage.identity import sign_detached

if TYPE_CHECKING:
    from bernstein.core.lineage.identity import AgentCard
    from bernstein.core.lineage.store import LineageStore

logger = logging.getLogger(__name__)


def _is_unsafe_path(artefact_path: str) -> str | None:
    """Return a reason string if the path is unsafe; ``None`` otherwise.

    Rules:

      * Absolute paths (``/...`` or ``C:\\...``) are rejected — lineage paths
        are repo-relative POSIX strings.
      * Any segment equal to ``..`` is rejected (path traversal).
      * Empty paths are rejected.
    """
    if not artefact_path:
        return "empty artefact_path"
    # POSIX absolute (`/foo`) or Windows-style drive prefix (`C:\foo`).
    if artefact_path.startswith("/") or (len(artefact_path) > 2 and artefact_path[1:3] == ":\\"):
        return "absolute artefact_path not allowed"
    # Normalise separator-style: lineage canonical is POSIX, so we treat
    # ``\`` as a separator too for the safety check (defence in depth).
    segments = artefact_path.replace("\\", "/").split("/")
    if any(seg == ".." for seg in segments):
        return "path traversal in artefact_path"
    return None


def _compute_operator_hmac(
    *,
    operator_hmac_key: bytes,
    entry_body_sans_hmac: dict[str, object],
) -> str:
    """Return the HMAC envelope hex-digest for an entry.

    The HMAC covers the JCS-canonical bytes of the entry with the
    ``operator_hmac`` field set to the empty string — the same body-sans-HMAC
    pattern used by ``bernstein.core.security.audit.AuditLog``.
    """
    canonical = json.dumps(entry_body_sans_hmac, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hmac.new(operator_hmac_key, canonical, hashlib.sha256).hexdigest()


class LineageRecorder:
    """Build, sign, and persist lineage entries for artefact writes.

    The recorder is stateless other than its dependencies on a ``LineageStore``
    and the operator HMAC key. Sharing one recorder across threads is safe;
    serialisation of writes is enforced by the store's flock.
    """

    def __init__(
        self,
        store: LineageStore,
        *,
        operator_hmac_key: bytes,
    ) -> None:
        self.store: LineageStore = store
        self._hmac_key: bytes = operator_hmac_key

    def record_write(
        self,
        *,
        artefact_path: str,
        new_content: bytes,
        agent_id: str,
        agent_card: AgentCard,
        private_key_pem: str,
        tool_call_id: str,
        span_id: str,
        artefact_kind: str = "file",
    ) -> str:
        """Record a single artefact write. Returns the entry hash.

        Args:
            artefact_path: Repo-relative POSIX path of the artefact written.
            new_content: The bytes that just landed on disk.
            agent_id: Bernstein agent slug (e.g. ``agent:claude-worker-3``).
            agent_card: Agent Card with the public key the auditor will use.
            private_key_pem: PEM-encoded Ed25519 private key for the agent.
            tool_call_id: Cross-link to the originating audit entry.
            span_id: OTel span hex; used both in the entry body and as the
                child span's parent context when telemetry is enabled.
            artefact_kind: One of ``ARTEFACT_KINDS``; defaults to ``file``.

        Raises:
            ValueError: When ``artefact_path`` is absolute or contains a
                path-traversal segment.
        """
        unsafe = _is_unsafe_path(artefact_path)
        if unsafe is not None:
            raise ValueError(unsafe)

        content_hash = "sha256:" + hashlib.sha256(new_content).hexdigest()
        tips = self.store.tip_set(artefact_path)
        # Recorder only ever chains to the single current tip. Forks are
        # surfaced upstream; merges are emitted by the Steward via an
        # explicit multi-parent ``record_merge`` call (out of scope for v1
        # core).
        parent_hashes: list[str] = list(tips.get("open", []))[:1]

        ts_ns = time.time_ns()

        # Build the body without the HMAC, compute it, then materialise the
        # final immutable entry. The HMAC binds the body (including parents
        # and content hash) so a substitution attack post-signing is caught
        # by both the JWS and the HMAC envelope independently.
        body_sans_hmac: dict[str, object] = {
            "v": 1,
            "artefact_path": artefact_path,
            "artefact_kind": artefact_kind,
            "content_hash": content_hash,
            "parent_hashes": parent_hashes,
            "agent_id": agent_id,
            "agent_card_kid": agent_card.kid,
            "tool_call_id": tool_call_id,
            "span_id": span_id,
            "ts_ns": ts_ns,
            "operator_hmac": "",
        }
        operator_hmac = _compute_operator_hmac(
            operator_hmac_key=self._hmac_key,
            entry_body_sans_hmac=body_sans_hmac,
        )

        entry = LineageEntry(
            v=1,
            artefact_path=artefact_path,
            artefact_kind=artefact_kind,
            content_hash=content_hash,
            parent_hashes=parent_hashes,
            agent_id=agent_id,
            agent_card_kid=agent_card.kid,
            tool_call_id=tool_call_id,
            span_id=span_id,
            ts_ns=ts_ns,
            operator_hmac=operator_hmac,
        )

        # Sign the entry-hash (sha256 over the canonical bytes, hex-encoded
        # with the ``sha256:`` prefix). The signed bytes are exactly the same
        # bytes the auditor recomputes — see ADR-009 §5.2.
        canonical = canonicalise(entry)
        digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
        jws = sign_detached(digest.encode("utf-8"), private_key_pem, kid=agent_card.kid)

        h = self.store.append(entry, jws=jws)

        # Best-effort OTel emission. ``start_span`` is a no-op when telemetry
        # has not been initialised, so this is safe in tests.
        try:
            from bernstein.core.observability.telemetry import start_span

            with start_span(
                "lineage.record_write",
                attributes={
                    "lineage.artefact_path": artefact_path,
                    "lineage.entry_hash": h,
                    "lineage.agent_id": agent_id,
                    "lineage.tool_call_id": tool_call_id,
                    "lineage.parent_hashes_count": len(parent_hashes),
                },
            ):
                pass
        except Exception as exc:  # pragma: no cover - telemetry must never break recording
            logger.debug("lineage OTel span emission failed: %s", exc)

        # Sanity check: the entry hash returned by the store must equal what
        # we'd recompute from the canonical bytes.
        assert h == entry_hash(entry), "store.append entry_hash mismatch"

        return h


__all__ = ["LineageRecorder"]
