# `clm` adapter — Dream Cyber Language Model gateway

Bernstein's adapter for [Dream Security](https://dreamgroup.com/)'s
proprietary Cyber Language Model (CLM) family. CLM is served behind
NVIDIA NIM (TensorRT-LLM + Triton), which exposes an
OpenAI-compatible HTTP API; the adapter is a thin shim that points
`aider` at a customer-side CLM gateway.

Phase 1 — adapter MVP. Phase 2 (mTLS, tool-calling, streaming
regression) is deferred until the `cluster-mtls-transport` ticket
lands.

---

## When to use this adapter

* Forward-deployed engagements where the CLM endpoint lives inside a
  customer network and the operator drives multi-step refactors,
  rule-generation, or connector work against it.
* Engineering-side workflows at Dream that would otherwise be
  ad-hoc prompts to internal endpoints, with no audit chain.

The same adapter shape works against any OpenAI-compatible gateway
(NIM, vLLM, llama.cpp server, local Ollama). For demo or
air-gapped runs, point `CLM_ENDPOINT` at a local Ollama and pick a
locally-pulled model.

---

## Configuration

### Required environment variables

| Variable | Purpose |
|----------|---------|
| `CLM_ENDPOINT` | Customer gateway base URL, e.g. `https://clm.internal.<customer>/v1/` |
| `CLM_TOKEN` | Scoped JWT issued by the customer's identity layer or by Dream's operator |
| `CLM_MODEL` | Model id passed through to the gateway (Dream ships a *family*; pass-through string) |

### Optional

| Variable | Default | Purpose |
|----------|--------:|---------|
| `CLM_REQUEST_TIMEOUT_SECONDS` | `60` | Per-request HTTP timeout |
| `CLM_MAX_RETRIES` | `2` | SDK-level retry budget for transient errors |

### Credential scoping

Register the env-var bundle in `.sdd/config/credential_scopes.yaml` so
each agent only inherits the keys it needs:

```yaml
enabled: true
known_keys:
  - CLM_ENDPOINT
  - CLM_TOKEN
  - CLM_MODEL
roles:
  backend:
    - CLM_ENDPOINT
    - CLM_TOKEN
    - CLM_MODEL
```

`CLM_TOKEN` is treated as opaque. It is **never** logged, persisted to
`.sdd/runtime/`, or written to audit / lineage records — only its
JWT `kid` is captured for traceability.

---

## Obtaining a scoped token

Tokens are issued by the customer's identity layer (or by Dream's
operator on a customer-provisioned jump box). Bernstein consumes
whatever token the customer issues; token issuance is out of scope
for this adapter.

The forward-deployed deployment geometry is:

1. Operator master keys stay on the operator's laptop or jump box.
2. The customer issues a short-lived JWT scoped to a single agent run.
3. The adapter forwards the scoped JWT to the spawned subprocess and
   only that subprocess.
4. Revoking the JWT mid-run terminates the run with a non-zero exit
   and a redaction-safe error message.

---

## Wire format

CLM exposes OpenAI-compatible HTTP (chat-completions, streaming SSE).
The adapter spawns `aider` configured to talk to the gateway via
standard `OPENAI_API_BASE` / `OPENAI_API_KEY` variables, with the
scoped `CLM_TOKEN` riding as the Bearer credential.

If a customer is running CLM behind a non-OpenAI-shaped wrapper, that
is a v2 follow-up — not in scope for Phase 1.

---

## Security caveats

* This adapter sends prompts to the customer-side CLM gateway. Treat
  every prompt as in-scope for the customer's data-flow review.
* Master keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.) are
  filtered out of the spawned environment — only the `CLM_*` bundle
  is forwarded.
* Audit and lineage records capture the prompt SHA and the token
  `kid`, never the token bytes. A redaction grep test in the
  integration suite enforces this.

---

## Phase 2 (deferred)

Tracked separately — blocked on `cluster-mtls-transport`:

* mTLS handshake against the customer's PKI.
* Tool-calling with the per-agent allowlist mapped to NIM's
  OpenAI-compatible `tools=[]` array.
* Streaming regression test guaranteeing lineage records contain the
  full assembled response, not just the first chunk.
