# Healthcare demo — triage decision-support config

## TL;DR

| Item | Value |
|---|---|
| Org | Northstar Health |
| Window | 2026-02-02 → 2026-02-14 |
| Primary artefact | `config/triage/decision_support.yaml` |
| Secondary artefact | `docs/article-11/technical_documentation.md` |
| Agents | clinical-rules-bot, hipaa-redactor, article11-docs-bot |
| Entries | 32 (mix of config + Article 11 docs edits) |
| Output | `expected-pack.zip` + `article12-mapping.md` |

## The procurement story

A digital-health vendor sells an AI-assisted ED triage tool. The hospital's
**clinical safety officer** and **DPO** have to sign off:

- HIPAA — every input touch on PHI must be tracked.
- EU AI Act high-risk classification (Annex III §5(a) — emergency services
  dispatch). **Article 11** demands technical documentation; **Article 12**
  demands an automatic event log retained for at least 6 months (10 years
  for high-risk).

Compliance can't accept a vendor statement. They need:

1. Article 11 technical-documentation evidence (`docs/article-11/...`)
   showing each Annex IV sub-clause is addressed and **who** wrote which
   paragraph.
2. Article 12 event-log evidence: every change to the triage thresholds,
   every PHI-redaction tightening, signed by the agent identity that made
   the change.
3. A mapping from Article 12 sub-paragraphs to specific log entries so the
   auditor can navigate the evidence without spelunking.

The demo ships exactly that, see `article12-mapping.md`.

## What's in `fixtures/`

| Path | Purpose |
|---|---|
| `log.jsonl` | 32 entries: triage threshold edits + Article 11 doc edits. |
| `signatures/...` | Detached Ed25519 JWS per entry. |
| `agent-cards/` | 3 Agent Cards. |

## Article 12 paragraph mapping

See [`article12-mapping.md`](./article12-mapping.md). Each Article 12
paragraph in EU Regulation 2024/1689 is mapped to one or more entry hashes
in `log.jsonl`. The mapping doubles as the auditor's index into the bundle.

## How to regenerate

```bash
uv run python examples/lineage/scripts/gen_demo_healthcare.py
uv run python examples/lineage/scripts/build_expected_pack.py
```

Deterministic — seed `20260201`, fixed UTC timestamps.

## How to run the demo

```bash
make -C examples/lineage demo-healthcare
```

Invokes:

1. `bernstein compliance pack` over the healthcare fixtures.
2. `bernstein-verify pack` on the bundle.
3. Comparison of the produced bundle vs the committed `expected-pack.zip`.

## Agents

| Agent ID | Role |
|---|---|
| `agent:clinical-rules-bot` | Edits triage thresholds in the YAML config. |
| `agent:hipaa-redactor` | Tightens PHI redaction rules on input fields. |
| `agent:article11-docs-bot` | Keeps the Article 11 technical doc in sync. |

Each agent has its own Ed25519 keypair; their public keys are in
`fixtures/agent-cards/<agent>.json`. Auditor verification uses these
cards — no need to talk to the operator's KMS.
