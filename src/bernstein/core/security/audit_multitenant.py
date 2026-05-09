"""Multi-tenant HMAC-chained audit-log export.

Bernstein's audit log is a single HMAC chain across every event the
orchestrator emits. Enterprise operators running bernstein on behalf of
multiple internal customers need to hand each customer (or each external
auditor) a slice that:

* Contains **only** that customer's events — no leakage of sibling tenants.
* **Re-verifies offline** so an auditor with the operator's HMAC key can
  replay-check every link without consulting the live orchestrator state.
* Carries a **tamper-evident anchor** (sha256 of the canonical JSONL) so a
  cross-tenant flip in the slice is detected even without the key.
* Is **byte-deterministic** — same input window + tenant id produces a
  byte-identical bundle on every run, so spot-audit reproducibility holds.

Design:

The original chain commits HMAC over ``prev || canonical(payload-without-
hmac)``. Filtering arbitrary events out of that chain breaks the linkage
because the dropped events' HMACs were prev-anchors for their successors.
We rebuild a **fresh slice-local chain** over only the matching events,
keyed by the same operator HMAC key. Each tenant slice is therefore a
self-contained HMAC chain rooted at the genesis sentinel.

The original HMAC of each emitted event is preserved as
``details._original_hmac`` so an auditor can still cross-reference back
to the source log when they have access to it. A flipped tenant id in the
exported slice is detectable because:

* Its ``hmac`` no longer matches HMAC(key, prev || canonical(stripped))
  in the slice-local chain, AND
* Its ``_original_hmac`` does not appear (or appears with a different
  payload) in the original log.

References:

* W3C Verifiable Credentials Data Model 2.0 — conceptually similar
  citation/proof split, but rejected as the wire format because VC v2 is
  RDF/JSON-LD-shaped and forces JSON-LD context resolution at verify
  time. Audit chains are line-oriented JSONL; a custom schema (see
  ``schemas/audit-multitenant-export-v1.json``) is leaner. The schema
  is versioned (``schema_version: 1.0.0``) so future migrations to VC v2
  or in-toto attestations stay open.
* RFC 3161 — Time-Stamp Protocol. Optional third-party timestamp token.
* IETF SCITT — future direction; not wired in v1.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bernstein.core.security.tenanting import normalize_tenant_id

logger = logging.getLogger(__name__)

#: Schema version emitted in the exported bundle. Bumped on any breaking
#: change to artefact field ordering, payload format, or HMAC input shape.
EXPORT_SCHEMA_VERSION: str = "1.0.0"

#: Genesis sentinel matching :mod:`bernstein.core.security.audit`.
_GENESIS_HMAC: str = "0" * 64

#: Canonical glob for daily HMAC-chained log files.
_JSONL_GLOB: str = "*.jsonl"

SignatureKind = Literal[
    "hmac-chain-only",
    "hmac-chain+rfc3161",
    "hmac-chain+offline-anchor",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TenantScopedExport:
    """Materialised tenant-scoped audit slice.

    Attributes:
        tenant_id: Normalized tenant identifier.
        since: Inclusive ISO-8601 lower bound of the export window.
        until: Exclusive ISO-8601 upper bound of the export window.
        event_count: Number of matching events.
        head_hmac: HMAC of the last event in the slice-local chain (or
            the genesis sentinel when the window is empty).
        head_sha256: SHA-256 over the canonical JSONL bytes of the slice.
            Tamper-evident even without the HMAC key.
        signature_kind: Which verifier path the bundle declares.
        bundle_bytes: The full canonical-JSON bundle bytes (matches what
            is written to disk when ``write=True``). Always available so
            tests/dry-runs can hash without disk I/O.
        bundle_path: On-disk path of the written bundle, or ``None`` when
            ``write=False``.
    """

    tenant_id: str
    since: str
    until: str
    event_count: int
    head_hmac: str
    head_sha256: str
    signature_kind: SignatureKind
    bundle_bytes: bytes
    bundle_path: Path | None = None

    @property
    def sha256(self) -> str:
        """SHA-256 of the on-disk bundle bytes."""
        return hashlib.sha256(self.bundle_bytes).hexdigest()


@dataclass(frozen=True, slots=True)
class TenantSliceVerification:
    """Outcome of an offline ``verify_tenant_slice`` call.

    Attributes:
        ok: True when every check passed.
        errors: Human-readable failure messages (empty when ``ok``).
        bundle: Parsed bundle dict (empty when reading itself failed).
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    bundle: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _canonical_event_payload(entry: dict[str, Any]) -> str:
    """Return the canonical JSON representation used as HMAC input.

    Matches the convention in :mod:`bernstein.core.security.audit`:
    ``json.dumps(entry, sort_keys=True)``. The ``hmac`` field is excluded
    upstream; this helper serialises the supplied dict as-is.
    """
    return json.dumps(entry, sort_keys=True)


def _compute_event_hmac(key: bytes, prev_hmac: str, payload: dict[str, Any]) -> str:
    """Compute HMAC-SHA256 over ``prev_hmac || canonical(payload)``.

    Args:
        key: Operator HMAC key bytes.
        prev_hmac: Hex-encoded prior event HMAC (or genesis sentinel).
        payload: Event dict *without* the ``hmac`` field.

    Returns:
        Hex-encoded HMAC of the chained payload.
    """
    serialised = (prev_hmac + _canonical_event_payload(payload)).encode()
    return _hmac.new(key, serialised, hashlib.sha256).hexdigest()


def _read_audit_events(audit_dir: Path) -> list[dict[str, Any]]:
    """Walk ``audit_dir/*.jsonl`` and return every parseable event in order.

    Lines that fail to parse are skipped silently (``logger.debug``).
    """
    events: list[dict[str, Any]] = []
    if not audit_dir.is_dir():
        return events
    for path in sorted(audit_dir.glob(_JSONL_GLOB)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Skipping unreadable audit file %s: %s", path, exc)
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.debug("Skipping malformed line in %s: %s", path, exc)
                continue
            if isinstance(entry, dict) and "hmac" in entry:
                events.append(entry)
    return events


def _event_tenant_id(event: dict[str, Any]) -> str:
    """Extract the canonical tenant id for an event.

    Looks at ``details.tenant_id`` first (the canonical opt-in path);
    falls back to ``DEFAULT_TENANT_ID`` via :func:`normalize_tenant_id`.
    """
    details = event.get("details") or {}
    raw = None
    if isinstance(details, dict):
        raw = details.get("tenant_id")
    return normalize_tenant_id(str(raw) if raw is not None else None)


def _event_in_window(event: dict[str, Any], since: str, until: str) -> bool:
    """Return True when ``event.timestamp`` falls in ``[since, until)``."""
    ts = str(event.get("timestamp", ""))
    if not ts:
        return False
    return since <= ts < until


def _filter_tenant_events(
    events: list[dict[str, Any]],
    tenant_id: str,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    """Filter to events that match ``tenant_id`` and ``[since, until)``.

    Stable order is preserved (chronological because the source log is
    append-only). If two events share a timestamp we fall back to the
    original ``hmac`` for determinism.
    """
    matched = [e for e in events if _event_tenant_id(e) == tenant_id and _event_in_window(e, since, until)]
    matched.sort(key=lambda e: (str(e.get("timestamp", "")), str(e.get("hmac", ""))))
    return matched


def _rebuild_slice_chain(
    events: list[dict[str, Any]],
    key: bytes,
) -> tuple[list[dict[str, Any]], str]:
    """Rebuild a slice-local HMAC chain over ``events`` keyed by ``key``.

    Each output event preserves the original event's user-facing fields
    (timestamp, event_type, actor, resource_type, resource_id, details)
    and adds:

    * ``details._original_hmac`` — the HMAC the event carried in the
      orchestrator-wide chain. Witness for cross-reference.
    * ``prev_hmac`` — the slice-local predecessor HMAC.
    * ``hmac`` — the slice-local HMAC.

    Args:
        events: Filtered events in chronological order (originals).
        key: Operator HMAC key.

    Returns:
        ``(rebuilt_events, head_hmac)``. ``head_hmac`` is the genesis
        sentinel when ``events`` is empty.
    """
    rebuilt: list[dict[str, Any]] = []
    prev = _GENESIS_HMAC
    for original in events:
        original_details = original.get("details") or {}
        if not isinstance(original_details, dict):
            original_details = {}
        new_details = dict(original_details)
        # Witness: stamp the original orchestrator-wide HMAC so an auditor
        # with access to the source log can cross-check.
        new_details["_original_hmac"] = str(original.get("hmac", ""))

        payload: dict[str, Any] = {
            "timestamp": str(original.get("timestamp", "")),
            "event_type": str(original.get("event_type", "")),
            "actor": str(original.get("actor", "")),
            "resource_type": str(original.get("resource_type", "")),
            "resource_id": str(original.get("resource_id", "")),
            "details": new_details,
            "prev_hmac": prev,
        }
        slice_hmac = _compute_event_hmac(key, prev, payload)
        emitted = dict(payload)
        emitted["hmac"] = slice_hmac
        rebuilt.append(emitted)
        prev = slice_hmac
    return rebuilt, prev


def _events_jsonl_bytes(events: list[dict[str, Any]]) -> bytes:
    """Serialise events as canonical JSONL (sorted keys, ``\\n`` newlines)."""
    if not events:
        return b""
    parts = [json.dumps(e, sort_keys=True, separators=(",", ":")) for e in events]
    return ("\n".join(parts) + "\n").encode("utf-8")


def _attach_signature(
    head_sha256: str,
    *,
    signature_kind: SignatureKind,
    rfc3161_token_b64: str | None,
    rfc3161_tsa_url: str | None,
    offline_anchor_iso: str | None,
) -> dict[str, Any]:
    """Build the detached signature block for the bundle.

    The block is added at the top level of the bundle and feeds the
    schema's ``signature`` field. The HMAC chain itself is the primary
    proof; this block adds optional third-party or air-gap evidence.
    """
    block: dict[str, Any] = {
        "signature_kind": signature_kind,
        "alg": "HMAC-SHA256",
        "rfc3161_token_b64": rfc3161_token_b64,
        "rfc3161_tsa_url": rfc3161_tsa_url,
        "offline_anchor": None,
    }
    if signature_kind == "hmac-chain+offline-anchor":
        ts = offline_anchor_iso or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        anchor_input = (head_sha256 + ts).encode()
        block["offline_anchor"] = {
            "anchored_at": ts,
            "anchor_sha256": hashlib.sha256(anchor_input).hexdigest(),
        }
    return block


def _canonical_bundle_bytes(bundle: dict[str, Any]) -> bytes:
    """Serialise the top-level bundle dict canonically.

    Stable rules:

    * ``json.dumps(..., sort_keys=True, separators=(',', ':'))``
    * Trailing ``\\n``.

    The trailing newline is included so callers concatenating multiple
    bundles do not run lines together.
    """
    return (json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Public: export
# ---------------------------------------------------------------------------


def export_tenant_slice(
    audit_dir: Path,
    tenant_id: str,
    *,
    since: str,
    until: str,
    key: bytes,
    output_dir: Path | None = None,
    signature_kind: SignatureKind = "hmac-chain-only",
    rfc3161_token_b64: str | None = None,
    rfc3161_tsa_url: str | None = None,
    offline_anchor_iso: str | None = None,
    write: bool = True,
) -> TenantScopedExport:
    """Build a tenant-scoped audit-chain export bundle.

    Pipeline:

    1. Walk every event in ``audit_dir`` (read-only).
    2. Filter to events whose ``details.tenant_id`` (after normalization)
       matches ``tenant_id`` and whose timestamp falls in ``[since, until)``.
    3. Rebuild a slice-local HMAC chain over the filtered events using
       ``key`` so the slice is offline-replay-verifiable.
    4. Emit a deterministic JSON bundle conforming to
       ``schemas/audit-multitenant-export-v1.json``.

    Args:
        audit_dir: Directory of HMAC-chained ``YYYY-MM-DD.jsonl`` files
            (typically ``.sdd/audit/``). Read-only.
        tenant_id: Tenant whose events to extract. Normalized via
            :func:`normalize_tenant_id`.
        since: ISO-8601 inclusive lower bound. String-compared against
            event timestamps (which are written in canonical UTC ISO-8601
            so lexical compare matches chronological).
        until: ISO-8601 exclusive upper bound.
        key: Operator HMAC key. The slice-local chain is keyed identically
            so existing operators reuse one secret across exports.
        output_dir: Where to write the bundle. Defaults to
            ``audit_dir.parent / 'evidence'`` (``.sdd/evidence/``).
        signature_kind: Which verifier path the bundle declares. ``hmac-
            chain-only`` is the default; pass ``hmac-chain+rfc3161`` plus
            a token to attach a TSA timestamp; pass ``hmac-chain+offline-
            anchor`` for air-gap deployments.
        rfc3161_token_b64: Base64-encoded DER TimeStampToken from a TSA.
            Required iff ``signature_kind == 'hmac-chain+rfc3161'``.
        rfc3161_tsa_url: URL of the TSA that issued the token.
        offline_anchor_iso: Override timestamp for the offline anchor
            (defaults to ``datetime.now(UTC)``). Tests use this for
            deterministic byte output.
        write: When False, build everything in-memory and skip the disk
            write — useful for ``--dry-run`` and tests.

    Returns:
        :class:`TenantScopedExport` with the serialized bundle bytes,
        chain anchor, and (when ``write=True``) the on-disk path.

    Raises:
        ValueError: ``since`` is not strictly less than ``until``, or
            ``tenant_id`` is empty after normalization, or
            ``rfc3161_token_b64`` is missing when the signature kind
            requires it.
    """
    if since >= until:
        raise ValueError(f"since={since!r} must be < until={until!r}")
    normalized_tenant = normalize_tenant_id(tenant_id)
    if not normalized_tenant:
        raise ValueError("tenant_id resolved to empty value after normalization")
    if signature_kind == "hmac-chain+rfc3161" and not rfc3161_token_b64:
        raise ValueError(
            "signature_kind='hmac-chain+rfc3161' requires rfc3161_token_b64",
        )

    all_events = _read_audit_events(audit_dir)
    matched = _filter_tenant_events(all_events, normalized_tenant, since, until)
    rebuilt, head_hmac = _rebuild_slice_chain(matched, key)

    events_canonical = _events_jsonl_bytes(rebuilt)
    head_sha256 = hashlib.sha256(events_canonical).hexdigest()

    signature_block = _attach_signature(
        head_sha256,
        signature_kind=signature_kind,
        rfc3161_token_b64=rfc3161_token_b64,
        rfc3161_tsa_url=rfc3161_tsa_url,
        offline_anchor_iso=offline_anchor_iso,
    )

    bundle: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "tenant_id": normalized_tenant,
        "audit_window": {"since": since, "until": until},
        "chain_anchor": {
            "genesis_prev_hmac": _GENESIS_HMAC,
            "head_hmac": head_hmac,
            "head_sha256": head_sha256,
        },
        "event_count": len(rebuilt),
        "events": rebuilt,
        "signature": signature_block,
    }
    bundle_bytes = _canonical_bundle_bytes(bundle)

    bundle_path: Path | None = None
    if write:
        target_dir = output_dir or (audit_dir.parent / "evidence")
        target_dir.mkdir(parents=True, exist_ok=True)
        # File name is deterministic: tenant_id is path-sanitized via
        # normalize_tenant_id (which strips whitespace) but we still
        # apply a conservative replace for filesystem safety.
        safe_tenant = normalized_tenant.replace("/", "_").replace("\\", "_")
        bundle_path = target_dir / (f"audit-multitenant-{safe_tenant}-{since}-{until}.json")
        bundle_path.write_bytes(bundle_bytes)
        logger.info(
            "Multi-tenant audit slice written tenant=%s events=%d path=%s",
            normalized_tenant,
            len(rebuilt),
            bundle_path,
        )

    return TenantScopedExport(
        tenant_id=normalized_tenant,
        since=since,
        until=until,
        event_count=len(rebuilt),
        head_hmac=head_hmac,
        head_sha256=head_sha256,
        signature_kind=signature_kind,
        bundle_bytes=bundle_bytes,
        bundle_path=bundle_path,
    )


# ---------------------------------------------------------------------------
# Public: verify
# ---------------------------------------------------------------------------


def _validate_bundle_envelope(bundle: dict[str, Any]) -> list[str]:
    """Top-level structural validation; returns human-readable errors."""
    errors: list[str] = []
    required = (
        "schema_version",
        "tenant_id",
        "audit_window",
        "chain_anchor",
        "event_count",
        "events",
        "signature",
    )
    for field_name in required:
        if field_name not in bundle:
            errors.append(f"missing required field: {field_name}")
    if errors:
        return errors

    if bundle["schema_version"] != EXPORT_SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: got {bundle['schema_version']!r}, expected {EXPORT_SCHEMA_VERSION!r}",
        )
    window = bundle.get("audit_window") or {}
    since = window.get("since")
    until = window.get("until")
    if since is None or until is None:
        errors.append("audit_window must include since and until")
    elif not isinstance(since, str) or not isinstance(until, str):
        errors.append("audit_window since/until must be ISO-8601 strings")
    elif since >= until:
        # Lexicographic compare matches chronological for canonical UTC ISO.
        errors.append(f"audit_window since={since!r} must be < until={until!r}")
    anchor = bundle.get("chain_anchor") or {}
    for required_anchor in ("genesis_prev_hmac", "head_hmac", "head_sha256"):
        if required_anchor not in anchor:
            errors.append(f"chain_anchor missing {required_anchor}")
    if not isinstance(bundle.get("events"), list):
        errors.append("events must be a list")
    return errors


def _verify_anchor_consistency(bundle: dict[str, Any]) -> list[str]:
    """Recompute head_sha256 from events and compare to the bundle anchor."""
    errors: list[str] = []
    events = bundle.get("events") or []
    canonical = _events_jsonl_bytes(events)
    expected_sha = hashlib.sha256(canonical).hexdigest()
    anchor = bundle.get("chain_anchor") or {}
    declared_sha = str(anchor.get("head_sha256", ""))
    if declared_sha != expected_sha:
        errors.append(
            f"head_sha256 mismatch: declared {declared_sha[:16]}…, recomputed {expected_sha[:16]}…",
        )
    return errors


def _verify_tenant_purity(
    bundle: dict[str, Any],
) -> list[str]:
    """Ensure every event in the slice carries the declared tenant id."""
    declared = normalize_tenant_id(str(bundle.get("tenant_id", "")))
    errors: list[str] = []
    for idx, event in enumerate(bundle.get("events") or []):
        details = event.get("details") or {}
        observed = normalize_tenant_id(
            str(details.get("tenant_id", "")) if isinstance(details, dict) else None,
        )
        if observed != declared:
            errors.append(
                f"events[{idx}]: tenant_id mismatch (declared {declared!r}, observed {observed!r})",
            )
    return errors


def _verify_chain(
    bundle: dict[str, Any],
    key: bytes,
) -> list[str]:
    """Re-derive each event's HMAC and confirm the slice-local chain."""
    errors: list[str] = []
    prev = _GENESIS_HMAC
    for idx, event in enumerate(bundle.get("events") or []):
        if not isinstance(event, dict):
            errors.append(f"events[{idx}]: expected object, got {type(event).__name__}")
            return errors
        stored_hmac = str(event.get("hmac", ""))
        recorded_prev = str(event.get("prev_hmac", ""))
        if recorded_prev != prev:
            errors.append(
                f"events[{idx}]: prev_hmac mismatch (expected {prev[:16]}…, got {recorded_prev[:16]}…)",
            )
            return errors
        stripped = {k: v for k, v in event.items() if k != "hmac"}
        expected_hmac = _compute_event_hmac(key, prev, stripped)
        if stored_hmac != expected_hmac:
            errors.append(
                f"events[{idx}]: HMAC mismatch (expected {expected_hmac[:16]}…, got {stored_hmac[:16]}…)",
            )
            return errors
        prev = stored_hmac
    anchor = bundle.get("chain_anchor") or {}
    declared_head = str(anchor.get("head_hmac", ""))
    if declared_head != prev:
        errors.append(
            f"head_hmac mismatch: declared {declared_head[:16]}…, recomputed {prev[:16]}…",
        )
    return errors


def _verify_signature_block(bundle: dict[str, Any]) -> list[str]:
    """Light structural checks on the signature block.

    Heavy crypto verification (RFC 3161 token chain) is not done here —
    operators feed the token to their own toolchain. The bundle merely
    asserts the kind + that the offline anchor is internally consistent.
    """
    errors: list[str] = []
    sig = bundle.get("signature") or {}
    kind = sig.get("signature_kind")
    if kind not in {
        "hmac-chain-only",
        "hmac-chain+rfc3161",
        "hmac-chain+offline-anchor",
    }:
        errors.append(f"unknown signature_kind: {kind!r}")
        return errors
    if kind == "hmac-chain+rfc3161":
        token = sig.get("rfc3161_token_b64")
        if not token or not isinstance(token, str):
            errors.append("rfc3161_token_b64 missing for signature_kind=rfc3161")
            return errors
        try:
            base64.b64decode(token, validate=True)
        except (ValueError, TypeError) as exc:
            errors.append(f"rfc3161_token_b64 not valid base64: {exc}")
    if kind == "hmac-chain+offline-anchor":
        anchor = sig.get("offline_anchor") or {}
        ts = str(anchor.get("anchored_at", ""))
        declared = str(anchor.get("anchor_sha256", ""))
        head_sha256 = str((bundle.get("chain_anchor") or {}).get("head_sha256", ""))
        recomputed = hashlib.sha256((head_sha256 + ts).encode()).hexdigest()
        if declared != recomputed:
            errors.append(
                "offline_anchor.anchor_sha256 does not match sha256(head_sha256 || anchored_at)",
            )
    return errors


def verify_tenant_slice(
    bundle_or_path: Path | bytes | dict[str, Any],
    *,
    key: bytes,
) -> TenantSliceVerification:
    """Re-verify a tenant-scoped audit slice offline.

    Runs without orchestrator state — the verifier needs only the bundle
    bytes and the operator's HMAC key. Performs four independent checks:

    1. Envelope structure (schema_version, required fields, types).
    2. Tenant purity — every event carries the declared tenant id.
    3. Chain integrity — re-derive each event's HMAC, confirm the chain
       links forward correctly, and confirm the declared ``head_hmac``
       matches the recomputed tail.
    4. Anchor consistency — recompute ``head_sha256`` from the canonical
       JSONL and compare. (Catches single-byte flips even when the key
       is leaked or the chain check is somehow bypassed.)
    5. Signature block sanity — base64 validity, offline anchor formula.

    Args:
        bundle_or_path: A path on disk, raw bundle bytes, or a parsed
            dict. The path/bytes branch parses canonical JSON.
        key: Operator HMAC key. Same key used to write the slice.

    Returns:
        :class:`TenantSliceVerification` carrying the parsed bundle and
        every observed failure.
    """
    bundle: dict[str, Any] = {}
    parse_errors: list[str] = []
    try:
        if isinstance(bundle_or_path, dict):
            bundle = bundle_or_path
        else:
            raw = bundle_or_path.read_bytes() if isinstance(bundle_or_path, Path) else bundle_or_path
            bundle = json.loads(raw.decode("utf-8"))
            if not isinstance(bundle, dict):
                parse_errors.append("bundle is not a JSON object")
    except (OSError, json.JSONDecodeError) as exc:
        parse_errors.append(f"failed to read/parse bundle: {exc}")

    if parse_errors:
        return TenantSliceVerification(ok=False, errors=parse_errors, bundle={})

    errors: list[str] = []
    errors.extend(_validate_bundle_envelope(bundle))
    if errors:
        return TenantSliceVerification(ok=False, errors=errors, bundle=bundle)

    errors.extend(_verify_anchor_consistency(bundle))
    errors.extend(_verify_tenant_purity(bundle))
    errors.extend(_verify_chain(bundle, key))
    errors.extend(_verify_signature_block(bundle))

    return TenantSliceVerification(ok=not errors, errors=errors, bundle=bundle)


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "SignatureKind",
    "TenantScopedExport",
    "TenantSliceVerification",
    "export_tenant_slice",
    "verify_tenant_slice",
]
