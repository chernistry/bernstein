# A2A capability cards and lineage interop

This page covers the cross-organisation A2A surface: signed **capability
cards** that one orchestrator publishes so a peer can decide whether to
delegate to it, and the **lineage chain wrapper** that carries a signed
Bernstein lineage chain through the A2A evidence envelope so a delegated run
stays auditable across an organisational boundary.

If you only have time for one paragraph: a capability card is a JCS-canonical
JSON body (RFC 8785) signed as a detached JWS (RFC 7515 A.5) with an Ed25519
key (RFC 8037 / EdDSA). It declares the issuer's identity, advertised tools,
enforced policies (cost cap, redaction tier, sandbox profile), the public key
that verifies it, and an expiry. A consumer fetches the card, verifies the
signature against a trusted-issuer set, and proceeds only if the advertised
policies meet its required policies. The lineage wrapper reuses the existing
`core/lineage/tracker_audit.py` HMAC chain.

This is distinct from the A2A v1.0 *agent card* served at
`/.well-known/agent.json` (see [A2A v1.0 signed agent
cards](../architecture/a2a.md)). The agent card describes wire formats and
auth schemes for protocol discovery; the capability card described here is a
delegation-trust manifest.

---

## Capability card fields

| Field | Meaning |
|---|---|
| `schema_version` | Card schema version (currently `"1"`). |
| `issuer` | Stable issuer id (organisation / orchestrator). |
| `name`, `description` | Human-readable issuer identity. |
| `advertised_tools[]` | Tool names the issuer exposes for delegation. |
| `policies.cost_cap_usd` | Maximum spend the issuer accepts per sub-task. |
| `policies.redaction_tier` | Redaction tier applied before artefacts leave the boundary. |
| `policies.sandbox_profile` | Sandbox profile delegated work runs under. |
| `public_key_pem` | SPKI PEM Ed25519 public key that verifies the signature. |
| `kid` | Key identifier carried in the JWS protected header. |
| `created_at`, `expires_at` | Issue and expiry timestamps (Unix seconds). |

The signature is a detached compact JWS (`header..signature`, empty payload)
over the JCS canonicalisation of the body, with the protected-header `typ`
set to `a2a-capability+jws` so it can never be confused with the
`agent-card+jws` identity-card context.

---

## Issue side

Generate a signed capability card for the local orchestrator:

```bash
bernstein interop a2a card \
  --issuer acme \
  --name "Acme Orchestrator" \
  --tool task_orchestration \
  --tool code_review \
  --cost-cap-usd 10 \
  --redaction-tier standard \
  --sandbox-profile container \
  --ttl-seconds 86400 \
  --output card.json
```

When no `--private-key` is supplied a fresh Ed25519 keypair is minted and the
private key is written to `card.json.key.pem` with `0600` permissions so the
operator can re-issue without changing identity. Pass `--private-key
card.json.key.pem` on a later run to keep the same key fingerprint.

The command prints the card's key **fingerprint** (`sha256:...`). Share that
fingerprint with peers out of band so they can pin you in their
trusted-issuer set.

---

## Consume side

Before delegating a sub-task to a peer, Bernstein:

1. fetches the peer's capability card;
2. verifies the detached JWS and rejects expired cards;
3. confirms the card's signing-key fingerprint is in the operator's
   trusted-issuer set;
4. proceeds only if the card's advertised policies meet the operator's
   required policies.

Programmatically:

```python
from bernstein.core.interop import (
    SignedCapabilityCard,
    PolicyRequirements,
    consume_peer_card,
)
from bernstein.core.interop.a2a_consume import PeerCardRejected

signed = SignedCapabilityCard.from_json(peer_card_json)
requirements = PolicyRequirements(
    max_cost_cap_usd=10.0,
    min_redaction_tier="standard",
    min_sandbox_profile="container",
)
try:
    consume_peer_card(
        signed,
        trusted_issuer_fingerprints={"sha256:..."},
        requirements=requirements,
    )
except PeerCardRejected as exc:
    # exc.reason is one of: signature, untrusted_issuer, policy
    raise
```

The policy gate is conservative and fails closed:

| Policy | Rule |
|---|---|
| Cost cap | Peer's advertised cap must be at or below the operator ceiling. |
| Redaction tier | Peer's tier must rank at or above the required tier. |
| Sandbox profile | Peer's profile must rank at or above the required profile. |

Tier and profile ordering (weakest to strongest):

- Redaction: `none < basic < standard < strict`
- Sandbox: `none < process < container < microvm`

An unranked custom value on either side fails the gate.

---

## CLI verify

Confirm a peer card is valid (signature plus, optionally, trust and policy):

```bash
# signature + expiry only
bernstein interop a2a verify --card peer-card.json

# also require the key be trusted and policies meet requirements
bernstein interop a2a verify \
  --card peer-card.json \
  --trusted-fingerprint sha256:... \
  --require-cost-cap-usd 10 \
  --require-redaction-tier standard \
  --require-sandbox-profile container
```

The command exits `0` when the card passes every requested check and `1`
otherwise, printing the specific reasons it failed. Add `--json` at the top
level (`bernstein --json interop a2a verify ...`) for machine-readable output.

---

## Lineage chain interop

When a Bernstein run delegates work to a peer over A2A, the run's signed
lineage chain travels inside the A2A evidence envelope under the
`bernstein.lineage_v2` field. The receiving side appends the delegated work
to its **own** chain with a cross-org boundary marker, so an auditor can see
exactly where one organisation's chain hands off to another.

The wrapper reuses the existing HMAC chain in
`core/lineage/tracker_audit.py`. No new signing primitive is introduced: the
boundary entry the receiver records is a normal signed tracker-audit entry
(`tracker_name = "a2a-cross-org-boundary"`) and verifies under the receiver's
operator HMAC key via the existing `bernstein lineage tracker-audit verify`.

Envelope payload shape (the value of `bernstein.lineage_v2`):

```json
{
  "schema_version": 1,
  "source_issuer": "acme",
  "chain_digest": "sha256:...",
  "entries": [ { "...tracker-audit entry..." } ]
}
```

`chain_digest` binds the carried entries and their order. The receiver
recomputes the digest on parse and rejects an envelope whose digest does not
match, so the chain cannot be tampered with in transit. The boundary marker
the receiver appends records `source_issuer` and `source_chain_digest`, so
the receiver's own chain is cryptographically bound to the exact source chain
it received.

Programmatically:

```python
from bernstein.core.interop import (
    wrap_lineage_chain,
    LineageEnvelope,
    append_cross_org_segment,
)
from bernstein.core.lineage.tracker_audit import TrackerActor, TrackerAuditLog

# Sender: wrap the local chain into an envelope field.
envelope = wrap_lineage_chain(sender_log, source_issuer="acme")
payload = envelope.to_envelope_field()  # splice into the A2A envelope

# Receiver: extract, then append a boundary marker to its own chain.
envelope = LineageEnvelope.from_envelope_field(received_payload)
actor = TrackerActor(session_id="r1", role="reviewer", model="...")
append_cross_org_segment(receiver_log, envelope, actor=actor, ticket_id="RECV-9")
```

---

## Trust model and limits

- The card carries its own public key, but carrying the key is **not**
  trust. A verifier must independently confirm the key fingerprint is in the
  operator's trusted-issuer set, which `consume_peer_card` enforces.
- Expiry is enforced at the verifier. A stale card is rejected even when its
  signature is otherwise valid, so a card cannot be replayed past its
  window.
- Fingerprints are computed over the raw Ed25519 public-key bytes, so they
  are stable across PEM whitespace differences.
- This surface does not replace the intra-org tracker handoff bus, and it
  does not bridge between MCP, ACP, and A2A.
