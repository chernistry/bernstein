# Service manifests at `/.well-known/*`

Bernstein's task server publishes machine-readable manifests so external
agents (Claude Code, Codex, third-party orchestrators) can discover its
endpoints, auth scheme, and capabilities without hand configuration.
This page is the catalog. For the cryptographic contract on the agent
card, see [A2A v1.0 — signed agent cards](../architecture/a2a.md).

| URL | Format | Purpose |
|---|---|---|
| `GET /.well-known/agent.json` | A2A v1.0 JSON, JCS-canonical | Signed agent card with auth, endpoints, capabilities, version. |
| `GET /.well-known/agent.json/keys` | JWKS (RFC 7517) | Public verification keys for the card signatures. |
| `GET /llms.txt` | Markdown | Human + LLM-friendly summary of the same surface. |

All three endpoints are unauthenticated. They live in
`AUTH_PUBLIC_PATHS` so any network caller can read them without
provisioning a token, and they expose only the public surface.

## Why it exists

Before this, an external agent talking to Bernstein had to be
hand-configured: someone had to know "task server is on 8052,
endpoints are POST /tasks, etc." Serving a static manifest closes the
loop and makes Bernstein a first-class platform other agents can
discover.

Adding the JCS + JWS layer (RFC 8785 + RFC 7515 + RFC 8037) closes a
second gap: the verifying peer no longer has to trust DNS plus TLS to
guarantee the card came from this operator's installation. The card
attests itself.

## How to use it

Hit each endpoint:

```bash
curl http://127.0.0.1:8052/.well-known/agent.json
curl http://127.0.0.1:8052/.well-known/agent.json/keys
curl http://127.0.0.1:8052/llms.txt
```

Sample `agent.json` (truncated):

```json
{
  "name": "bernstein",
  "description": "Bernstein orchestrates short-lived CLI coding agents...",
  "version": "1.10.5",
  "protocolVersion": "1.0",
  "url": "http://127.0.0.1:8052",
  "supportedInterfaces": ["HTTP+JSON"],
  "securitySchemes": [
    {"id": "bearer-jwt", "type": "http", "scheme": "Bearer", "required": true},
    {"id": "mtls", "type": "mutualTLS", "scheme": "mtls", "required": false}
  ],
  "endpoints": [
    {"method": "POST", "path": "/tasks", "summary": "Create a new task..."},
    {"method": "GET",  "path": "/tasks", "summary": "List tasks..."}
  ],
  "signatures": [
    {
      "kid": "agent-bernstein-orchestrator",
      "alg": "EdDSA",
      "typ": "agent-card+jws",
      "jws": "<base64url-header>..<base64url-signature>"
    }
  ]
}
```

The JWKS body follows RFC 7517:

```json
{
  "keys": [
    {
      "kty": "OKP",
      "crv": "Ed25519",
      "alg": "EdDSA",
      "use": "sig",
      "kid": "agent-bernstein-orchestrator",
      "x": "<base64url SPKI raw>"
    }
  ]
}
```

`llms.txt` is the same information rendered as markdown for an LLM to
parse, plus a short prose description.

The contents of `agent.json` and `llms.txt` are built from the same
in-module endpoint table at request time (`_ENDPOINTS` in
`src/bernstein/core/routes/well_known.py`); a regression test asserts
the two cannot drift.

## Configuration

| Knob | Default | Purpose |
|---|---|---|
| `BERNSTEIN_PUBLIC_BASE_URL` | `http://127.0.0.1:8052` | URL advertised in the card body. |
| `BERNSTEIN_AGENT_CARD_KEY_DIR` | `.bernstein/keys` | On-disk directory for the signing keypair. |

To customise the manifest content beyond the env knobs, edit the
endpoint table or the body builder in
`src/bernstein/core/routes/well_known.py`.

## Limitations

- One global manifest per server. No per-tenant customisation.
- Plugin / adapter manifests are **not** aggregated — only the
  task-server's own surface is published.
- The manifest is static at boot. Endpoints added by hot-loaded
  plugins after startup do not appear until restart.
- One signing key per installation. Multi-tenant signing (per-tenant
  `kid`) is not modelled here.

## Related

- A2A v1.0 contract and JWS shape:
  [architecture/a2a.md](../architecture/a2a.md).
- Persistent keystore semantics, rotation grace, OS-level perms:
  [security/keystore.md](../security/keystore.md).
- Source: `src/bernstein/core/routes/well_known.py`,
  `src/bernstein/core/security/agent_card_signer.py`,
  `src/bernstein/core/security/agent_card_keystore.py`.
- Auth middleware (RFC 8707 audience binding):
  `src/bernstein/core/security/auth_middleware.py`.
