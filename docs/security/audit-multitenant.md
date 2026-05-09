# Multi-tenant audit-chain export

Bernstein writes one HMAC-chained audit log per orchestrator instance.
When an enterprise operator runs bernstein on behalf of multiple internal
customers, every customer sees the same chain. That is fine for the
operator's internal compliance posture but it does not let them hand a
specific customer (or that customer's external auditor) a slice of the
log without leaking sibling tenants.

`bernstein audit export --tenant <id>` produces a tenant-scoped slice
that:

- Contains only events tagged with the requested `tenant_id`.
- Re-chains those events over a slice-local HMAC so an auditor can
  replay-verify offline using only the operator's HMAC key.
- Carries a tamper-evident SHA-256 anchor over the canonical JSONL bytes
  (catches single-byte flips even without the key).
- Optionally attaches an RFC 3161 TimeStampToken from a third-party TSA,
  or a deterministic offline anchor for air-gapped deployments.

Bundles are byte-deterministic — the same input window + tenant id
produces a byte-identical bundle on every run.

## Tagging events with `tenant_id`

The export filters on `details.tenant_id`. To enable per-tenant export,
add the tenant id to every event your code emits:

```python
audit_log.log(
    "task.created",
    actor="alice",
    resource_type="task",
    resource_id="T-1",
    details={"tenant_id": "acme", ...},
)
```

Events that omit `details.tenant_id` are treated as belonging to the
`default` tenant (matching `normalize_tenant_id` in
`src/bernstein/core/security/tenanting.py`). This keeps the rollout
incremental — operators can switch on multi-tenant tagging without
breaking pre-existing chains.

## CLI usage

### Bare HMAC chain (most common)

```bash
bernstein audit export \
    --tenant acme \
    --since 2026-08-01T00:00:00+00:00 \
    --until 2026-09-01T00:00:00+00:00 \
    --output .sdd/evidence/
```

### With RFC 3161 third-party timestamp

Get a TimeStampToken from any RFC 3161 TSA (FreeTSA, DigiCert, SwissSign,
etc.). Save the base64-encoded DER token to a file, then:

```bash
bernstein audit export \
    --tenant acme \
    --since 2026-08-01T00:00:00+00:00 \
    --until 2026-09-01T00:00:00+00:00 \
    --signature-kind hmac-chain+rfc3161 \
    --rfc3161-token /path/to/tsa.token.b64 \
    --rfc3161-tsa-url https://freetsa.org/tsr
```

The bundle records the token verbatim. The verifier confirms it is valid
base64. Cryptographic verification of the token chain (RFC 3161 §2.4.2)
is delegated to the operator's existing TSA toolchain (e.g. `openssl ts
-verify`).

### Offline anchor (air-gap deployments)

For deployments that cannot reach a public TSA, attach a deterministic
local anchor. Pass `--signature-kind hmac-chain+offline-anchor`. The
anchor is `sha256(head_sha256 || anchored_at_iso)`. It does not certify
wall-clock truth (an attacker with the bundle can recompute it) but it
ties the chain head to a specific operator-attested timestamp inside the
deterministic JSON.

```bash
bernstein audit export \
    --tenant acme \
    --since 2026-08-01T00:00:00+00:00 \
    --until 2026-09-01T00:00:00+00:00 \
    --signature-kind hmac-chain+offline-anchor
```

Note: the default offline anchor uses `datetime.now(UTC)` so two runs
produce different bundles. To get byte-identical air-gap bundles, pass
`offline_anchor_iso` through the Python API directly.

### Dry-run

`--dry-run` builds the bundle in-memory and prints the manifest without
writing to disk. Useful for spot-checking a window before shipping.

## Wire format

The bundle is a single JSON object that conforms to
`schemas/audit-multitenant-export-v1.json` (JSON Schema draft-07).

Top-level fields:

| Field            | Type    | Description                                                         |
| ---------------- | ------- | ------------------------------------------------------------------- |
| `schema_version` | string  | Pinned to `1.0.0`. Bumped on any breaking change.                   |
| `tenant_id`      | string  | Normalized tenant identifier.                                       |
| `audit_window`   | object  | `{since, until}` — ISO-8601 strings; since < until.                 |
| `chain_anchor`   | object  | `{genesis_prev_hmac, head_hmac, head_sha256}`.                      |
| `event_count`    | integer | Number of events in the slice.                                      |
| `events`         | array   | Slice events in chronological order, with `_original_hmac` witness. |
| `signature`      | object  | Detached anchor block.                                              |

Each event preserves the original orchestrator-wide HMAC at
`details._original_hmac` so an auditor with access to the source log can
cross-reference back. The slice itself is re-chained — `prev_hmac` /
`hmac` link to the slice-local chain, not the orchestrator-wide one.

## Verifying offline

```python
from pathlib import Path

from bernstein.core.security.audit import load_or_create_audit_key
from bernstein.core.security.audit_multitenant import verify_tenant_slice

key = load_or_create_audit_key()  # operator's HMAC key
result = verify_tenant_slice(Path("path/to/bundle.json"), key=key)
if not result.ok:
    for err in result.errors:
        print("FAIL:", err)
    raise SystemExit(1)
print("OK", result.bundle["event_count"], "events")
```

The verifier runs five independent checks:

1. **Envelope structure** — required fields, schema version, ISO-8601
   ordering of `audit_window`.
2. **Tenant purity** — every event in the slice carries the declared
   `tenant_id`.
3. **Chain integrity** — re-derive each event's HMAC; confirm
   `prev_hmac` linkage; confirm `chain_anchor.head_hmac` equals the
   recomputed tail.
4. **Anchor consistency** — recompute `head_sha256` from canonical
   JSONL bytes and compare.
5. **Signature block sanity** — base64 validity for RFC 3161 tokens;
   `sha256(head_sha256 || anchored_at)` for offline anchors.

A failure on any of those checks flips `result.ok` to `False` and
appends a human-readable message to `result.errors`.

## What is NOT in v1

- **Public-key signatures** (cosign, Sigstore, SCITT receipt). The
  primary proof is HMAC, which requires the auditor to share the key
  with the operator. Future versions can layer a public-key signature
  over `head_sha256` so a key-less auditor can still validate
  authenticity.
- **In-toto attestation wrapping**. The schema is custom because
  line-oriented JSONL chains do not fit the in-toto subject/predicate
  shape cleanly. Migrating later is a `schema_version` bump.
- **Live RFC 3161 TSA fetch**. The CLI takes a pre-fetched token. Wiring
  bernstein to call a TSA itself would couple the export to network
  policy.

## Compliance mapping (one-line)

- **EU AI Act Art. 12** — covered by `bernstein audit export
  --article-12 ...` (see `docs/security/AUDIT.md`); the multi-tenant
  export complements it for slicing per-customer.
- **DORA Art. 9 / Art. 28** — the slice + RFC 3161 token satisfies
  third-party register evidence.
- **SR 11-7** — the chain + sha256 anchor is the model audit trail.

## References

- W3C Verifiable Credentials Data Model 2.0
  (https://www.w3.org/TR/vc-data-model-2.0). Conceptually similar
  proof-on-claim split. Rejected as the primary wire format because VC
  v2 is RDF/JSON-LD-shaped and forces context resolution at verify
  time. Migration path remains open.
- RFC 3161 — Time-Stamp Protocol
  (https://www.rfc-editor.org/rfc/rfc3161).
- IETF SCITT — Supply Chain Integrity, Transparency, and Trust
  (https://datatracker.ietf.org/wg/scitt). Forward-looking direction
  for transparency-log integration.
