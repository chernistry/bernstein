# Air-gap installation

Bernstein is designed to run on systems that cannot reach the public
internet. The same wheel works in either mode — what changes is the
egress policy. This guide is for forward-deployed engineers (FDEs)
delivering Bernstein to sovereign customers, and for operators
maintaining an air-gap environment.

There are three pieces:

1. A **wheelhouse** — every wheel in Bernstein's pinned dependency
   closure plus the bernstein wheel itself, sitting in one directory
   ready for `pip install --no-index`.
2. A **signed manifest** — `MANIFEST.json` lists every wheel and its
   sha256, plus per-wheel `.sig` detached signatures the customer's
   compliance team verifies before install.
3. A **runtime profile** — `bernstein run --profile airgap` flips the
   default egress policy from "any" to "none". Network destinations
   that are explicitly approved are listed via `--allow-network`.

## On the build host (with internet)

You need `uv` and Python 3.12+ available. The build host is the only
machine that needs PyPI access.

```bash
# Build the wheelhouse (downloads every wheel in the closure + bernstein)
python scripts/build_airgap_wheelhouse.py --version 1.9.4

# Sign every wheel + the manifest with cosign
COSIGN_KEY=/secure/path/cosign.key \
  bash scripts/sign_airgap_wheelhouse.sh dist/airgap-wheelhouse/1.9.4
```

Result: `dist/airgap-wheelhouse/1.9.4/` containing

```
bernstein-1.9.4-py3-none-any.whl
bernstein-1.9.4-py3-none-any.whl.sig
fastapi-0.115.x-py3-none-any.whl
fastapi-0.115.x-py3-none-any.whl.sig
... (all transitive dependencies + their sigs) ...
MANIFEST.json
MANIFEST.sig
```

Copy this directory onto encrypted media. Bring the public key
(PEM) separately so the customer can verify in advance.

## On the customer site (no internet)

Mount the encrypted media. Verify before installing — never run
`pip install` against a wheelhouse you have not verified.

```bash
# 1. Confirm checksums against the manifest, signatures against the key.
bernstein verify ./airgap-wheelhouse/1.9.4 \
  --ca-pubkey ./bernstein-release.pub \
  --require-signatures

# 2. Install with no PyPI access.
python -m venv .venv && source .venv/bin/activate
pip install --no-index --find-links ./airgap-wheelhouse/1.9.4 bernstein

# 3. Sanity check.
bernstein --version
```

The verify step is non-zero on any sha256 mismatch or signature
failure and names the offending wheel in the error message.

## Running with `--profile airgap`

The profile flips the defaults that matter for air-gap:

- `--allow-network none` (deny every outbound)
- MCP catalog entries are treated as opt-in only
- Memo store path is pinned to `.sdd/runtime/memo/` (no `~/.cache/`)

The profile does not change the bernstein binary. The same wheel
runs both modes.

```bash
# Pure local-only run against a local Ollama instance.
bernstein run --profile airgap --allow-network 127.0.0.1:11434 \
  --goal "Refactor my-detection-rule.yml so the selection clause is stricter"
```

If a plan tries to use an adapter whose endpoint is not on the
allow-list, Bernstein refuses to spawn that agent and exits non-zero
with the destination in the error:

```
NetworkPolicyDenied: network egress denied by policy: api.cloudflare.com:443 (from adapter:Cloudflare Agents)
```

## Allow-list syntax

Repeat `--allow-network` for each rule:

| Token | Meaning |
| --- | --- |
| `127.0.0.1` | Loopback only |
| `10.0.0.0/8` | A whole CIDR block (internal cluster) |
| `ollama.local:11434` | One specific host:port |
| `none` | Explicit deny-all (the `--profile airgap` default) |
| `any` | Opt out of the gate — back-compat default outside `--profile airgap` |

Default outside `--profile airgap` is `any`, so existing scripts
keep working unmodified.

## Re-signing on the customer side

A customer who does not trust the upstream signing key (or wants
to layer their own audit) re-signs the wheelhouse with their own key:

```bash
COSIGN_KEY=/secure/customer-key.key \
  bash scripts/sign_airgap_wheelhouse.sh ./airgap-wheelhouse/1.9.4

# Bernstein verify accepts an alternative public key:
bernstein verify ./airgap-wheelhouse/1.9.4 --ca-pubkey ./customer.pub
```

The detached signature scheme means we never bury keys inside the
wheel artefacts themselves.

## Adding customer-internal wheels to the bundle

Customer-built wheels (private packages, internal forks) drop into
the same directory and get signed alongside the upstream wheels.
Re-run the sign step after copying. Update `MANIFEST.json` by re-
running `python scripts/build_airgap_wheelhouse.py` with the same
`--output` path so the manifest picks up the additions.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `pip install` resolves to PyPI anyway | Forgot `--no-index` | Always pass `--no-index --find-links <dir>` |
| `bernstein verify` reports `missing signature` | The directory was copied without `.sig` files | Copy the entire wheelhouse, including signatures |
| `NetworkPolicyDenied: ...` at adapter spawn | Endpoint not on allow-list | Add `--allow-network <host>` or pick a local adapter |
| `bernstein run` exits with `--profile` not recognised | Older bernstein version | Upgrade to ≥ 1.9.4 |

## Future scope

Phase 2 (separate ticket) adds:

- `bernstein wheelhouse build` and `bernstein wheelhouse verify`
  subcommands that wrap these scripts so operators don't need to
  know they exist as scripts on disk.
- `bernstein doctor airgap` — a battery of self-checks (no-egress
  during a plan, MCP catalog all-off, memo store local-only, audit
  HMAC valid).
- GPG verifier for sites that prefer GPG to sigstore.

Phase 1 is complete and ready for FDE-led demos. Phase 2 is the
"production-ready operator experience" follow-up.
