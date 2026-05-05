# `clm` adapter — Cyber Language Model gateway

Bernstein's adapter for sovereign-AI vendors that ship a customer-side
**Cyber Language Model (CLM)** — a family of LLMs trained on cyber
telemetry and served behind NVIDIA NIM (TensorRT-LLM + Triton).
NIM exposes an OpenAI-compatible HTTP API; the adapter is a thin
shim that points `aider` at a customer-side CLM gateway.

Phase 1 shipped the adapter MVP. Phase 2 partial added the
tool-calling allowlist + streaming regression. Phase 2.5 (this
document includes it) adds opt-in **mTLS** to the customer gateway,
reusing the `TLSConfig` plumbing introduced by the
`cluster-mtls-transport` ticket.

---

## When to use this adapter

* Forward-deployed engagements where the CLM endpoint lives inside a
  customer network and the operator drives multi-step refactors,
  rule-generation, or connector work against it.
* Engineering-side workflows that would otherwise be ad-hoc prompts
  to internal endpoints, with no audit chain.

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
| `CLM_TOKEN` | Scoped JWT issued by the customer's identity layer or by the operator |
| `CLM_MODEL` | Model id passed through to the gateway (a CLM vendor typically ships a *family*; pass-through string) |

### Optional

| Variable | Default | Purpose |
|----------|--------:|---------|
| `CLM_REQUEST_TIMEOUT_SECONDS` | `60` | Per-request HTTP timeout |
| `CLM_MAX_RETRIES` | `2` | SDK-level retry budget for transient errors |
| `CLM_CERT_FILE` | _(unset)_ | Path to the worker's PEM-encoded client certificate (mTLS). Required when any of the three mTLS files is set. |
| `CLM_KEY_FILE` | _(unset)_ | Path to the matching PEM-encoded private key. File mode 0600. |
| `CLM_CA_FILE` | _(unset)_ | Path to the customer's CA bundle used to verify the gateway's server certificate. |
| `CLM_VERIFY_MODE` | `required` | One of `required` / `optional` / `disabled`. Mirrors `TLSConfig.verify_mode`. Use `disabled` only for staged rollouts; **never** in a production sovereign deployment. |

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

Tokens are issued by the customer's identity layer (or by the
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

## mTLS (Phase 2.5)

Sovereign customers commonly require the worker side of any agent
session to present a client certificate signed by their internal CA
during the TLS handshake. The CLM adapter supports this via the
`CLM_CERT_FILE` / `CLM_KEY_FILE` / `CLM_CA_FILE` triple — the same
PEM-encoded files the operator's PKI already issues for cluster
node-to-node mTLS.

### How it wires through

Aider talks to NIM via the OpenAI Python SDK, which dispatches every
request through `httpx.Client`. httpx 0.28+ deliberately does **not**
read TLS material from environment variables, so the adapter wraps the
spawn through a small launcher
(`bernstein.adapters.clm_tls_launcher`) that:

1. Reads the `CLM_*_FILE` triple inside the spawned subprocess.
2. Builds an `ssl.SSLContext` via
   `bernstein.core.protocols.cluster.cluster_tls.build_httpx_client_kwargs`
   — the same helper the cluster transport uses, so there is exactly
   one place that knows how to produce a worker-side mTLS context.
3. Monkey-patches `httpx.Client.__init__` and
   `httpx.AsyncClient.__init__` to default to that context for any
   constructor call that does **not** explicitly override `verify=`.
4. Hands off to aider via `runpy.run_module("aider")` so the patch
   survives into the OpenAI SDK call sites.

The launcher is only inserted into the spawn command when all three
`CLM_*_FILE` variables are set. A *partial* triple is rejected at
spawn time with `ClmConfigError` — operator-error category, not a
silent fallback to plain HTTP.

### Configuration example

```bash
export CLM_ENDPOINT="https://clm.internal.<customer>/v1/"
export CLM_TOKEN="$(cat /var/run/secrets/clm/jwt)"
export CLM_MODEL="clm-7b-instruct"

# mTLS: the operator's PKI emits these on a customer-provisioned jump box.
export CLM_CERT_FILE=/etc/bernstein/pki/worker.crt
export CLM_KEY_FILE=/etc/bernstein/pki/worker.key
export CLM_CA_FILE=/etc/bernstein/pki/customer-ca.bundle.crt
# Optional: override the verify mode (defaults to required).
# export CLM_VERIFY_MODE=required
```

### Security caveats specific to mTLS

* `CLM_CERT_FILE` and `CLM_KEY_FILE` paths are added to the adapter's
  redaction set: their *values* (paths) leak deployment topology and
  must not appear in lineage / audit / `.sdd/runtime/` records. The
  files themselves stay where the operator's PKI placed them.
* The private key file should be mode `0600` and owned by the user
  account the operator runs Bernstein under. `bernstein cluster
  bootstrap-ca` enforces this for self-hosted internal clusters; for
  customer-issued material the customer's PKI is responsible.
* `CLM_VERIFY_MODE=disabled` accepts any peer certificate (still TLS,
  but no peer-cert verification). It exists for staged rollouts —
  do not ship it.

### Tested handshake paths

The integration suite (`tests/integration/adapters/test_adapter_clm_with_fake_nim.py`)
covers two acceptance criteria from the Phase 2.5 ticket:

* **Positive** — a worker carrying the matching client cert completes
  the TLS handshake against a `verify_mode='required'` fake NIM and
  receives a 200 response.
* **Negative** — a worker that trusts the CA but presents no client
  cert is rejected at the handshake, surfacing as `httpx.HTTPError`
  before any chat-completions endpoint runs.

---

## Limitations

* OpenAI-compatible HTTP only. A non-OpenAI-shaped CLM gateway is a v2
  follow-up — not in scope here.
* Aider is the spawned subprocess. Switching to a different driver
  CLI requires a separate adapter shim.
* mTLS uses one client cert per spawn. Per-request cert rotation is
  not supported — lifecycle is the operator's PKI's job.
* Token issuance is the customer's identity layer. Bernstein consumes
  whatever JWT the customer issues and does no rotation of its own.
* Streaming responses are assembled and emitted to lineage as a
  single record. Per-chunk lineage is not in v1.

## Related

* Source: `src/bernstein/adapters/clm.py`,
  `src/bernstein/adapters/clm_tls_launcher.py`
* Shared TLS plumbing: `src/bernstein/core/protocols/cluster/cluster_tls.py`
* [Cluster mTLS setup](../cluster/mtls-setup.md) — same `TLSConfig`
  the cluster transport uses
* [Artifact lineage trail](../concepts/artifact-lineage.md) — what
  the adapter produces per agent invocation
* PRs #1012 (Phase 1), #1016 (Phase 2 partial — tools + streaming),
  #1022 (Phase 2.5 — mTLS)
* Tickets: `2026-05-05-feat-clm-agent-adapter.md`,
  `2026-05-05-feat-clm-adapter-phase-2-5-mtls.md`
