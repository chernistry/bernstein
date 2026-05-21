# Fintech demo - `src/payments/flow.py`

## TL;DR

| Item | Value |
|---|---|
| Org | Acme Bank |
| Window | 2026-01-13 → 2026-01-27 (2 weeks) |
| Artefact | `src/payments/flow.py` |
| Agents | audit-helper, code-reviewer, security-scanner, docs-bot |
| Entries | 30 main + 1 rogue parallel-edit |
| Output | `expected-pack.zip` (Article 12 evidence bundle) |

## The procurement story

Acme Bank's CISO wants to approve a coding-agent pilot. Compliance asks the
hard question: **how do we prove which agent wrote which line, and that the
chain hasn't been tampered with?**

The flow:

1. **CISO** receives the proposed Bernstein adoption package.
2. **Compliance officer** requests an evidence sample: "Show me two weeks of
   real changes to a sensitive file."
3. **Engineering** runs `bernstein compliance pack` against
   `src/payments/flow.py` for the 2-week window and ships
   `acme-q1-payments-evidence.zip` back to compliance.
4. **External auditor** (SOC2 Type II reviewer) takes the bundle to an
   air-gapped laptop, runs `bernstein-verify pack`, and the tool prints
   `PASS - 30 entries, 4 agents, 0 unresolved forks`.
5. The **rogue-agent fixture** is the demo of failure mode: when a parallel
   edit lands without a merge, `bernstein-verify forks` flags it and the CI
   gate blocks the PR. The auditor sees: *the system catches what we asked
   for*.

This is the artefact that unblocks the procurement loop. Without it, the
buyer has no way to discharge their record-keeping obligation.

## What's in `fixtures/`

| Path | Purpose |
|---|---|
| `log.jsonl` | 30 lineage entries, JCS-canonical JSON per line, Ed25519-signed. |
| `signatures/<aa>/<full>/sha256_<hash>.jws` | Detached JWS (RFC 7515) per entry. |
| `agent-cards/<agent>.json` | A2A v1.0 Agent Cards for the 4 (+1 rogue) agents. |
| `rogue-agent.jsonl` | 1 entry that branches from entry #14. Used to exercise `bernstein-verify forks`. |
| `signatures-rogue/` | Signature for the rogue entry, kept separate so the main log verifies cleanly. |

## How to regenerate

```bash
uv run python examples/lineage/scripts/gen_demo_fintech.py
uv run python examples/lineage/scripts/build_expected_pack.py
```

Output is deterministic: fixed seed (`20260101`), fixed UTC timestamps,
fixed RFC 8785 canonicalisation. Re-running yields byte-identical fixtures
and a byte-identical `expected-pack.zip`.

## How to run the demo

```bash
make -C examples/lineage demo-fintech
```

This invokes the real CLIs (built by parallel agents B + C + D):

1. `bernstein compliance pack` - bundle the fixtures into an Article 12 zip.
2. `bernstein-verify pack` - third-party verification of the bundle.

Expected exit code: `0`. Expected stdout: `PASS`.

## Demonstrating the fork detection

```bash
bernstein-verify forks src/payments/flow.py \
  --lineage-dir examples/lineage/fintech/fixtures \
  --extra-log    examples/lineage/fintech/fixtures/rogue-agent.jsonl
```

Expected output: a `FORK DETECTED` line citing entry #14 as the shared
parent and listing the two divergent child entry hashes. Exit code `1`.

## Mapping to the wider lineage v1 plan

This demo lives under `examples/lineage/fintech/`. Demo authoring is owned
by **agent E** in ADR-009 §13. Schema dependencies are pinned to
`feat/lineage-v1-schema-lock`: `LineageEntry`, `canonicalise`, `entry_hash`,
`AgentCard`, `generate_keypair`, `sign_detached`, `verify_detached`.

No other Bernstein modules are imported. The demo runs on a clean checkout
without any of the recorder / store / pack / verify code present.
