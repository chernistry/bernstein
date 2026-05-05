# Artifact lineage trail

Every write an agent produces is recorded as a `LineageRecord` linking
the output back to the producing prompt, the input artefacts the agent
read, the model, the run, and the cost. The chain is HMAC-signed and
artefact-indexed, so "which agent run, which prompt, which source
files produced this broken line?" becomes a one-command lookup.

This page covers schema-v1 lineage. The customer-key signature and
regulator-class fields shipped on top of v1 live in
[Regulator-class lineage](../compliance/regulatory-lineage.md).

## Why it exists

The HMAC audit log is event-ordered: "agent X wrote file Y at time T."
That is enough for forensics, not enough for compliance. EU AI Act,
DORA, and SOC2 audits ask "show me the chain for this artefact" —
producing prompt, input bytes, model, cost. Lineage is that chain.

It is also the tool we reach for when:

- Cross-model verifier flags a divergence and we need to see which
  prompt + which input file produced it.
- A regression lands and we need to bisect by producer.
- We want to attribute tokens / cost back to the originating task.

## How to use it

Lineage records are emitted automatically by the WAL writer on every
`apply_patch`-style tool call. There is nothing to enable for the
write side. To read the chain back, use the `lineage` CLI:

```bash
# Walk the chain for one file (or one line within it)
bernstein lineage src/foo.py
bernstein lineage src/foo.py:42

# Filter by run
bernstein lineage src/foo.py --run r-2026-05-05

# Export for a regulator (HTML / CSV / JSON-LD)
bernstein lineage export r-2026-05-05 --format html  --output /tmp/audit.html
bernstein lineage export r-2026-05-05 --format csv   --output /tmp/audit.csv
bernstein lineage export r-2026-05-05 --format jsonld --output /tmp/audit.jsonld

# Re-verify the HMAC + customer-key chain (Phase 2)
bernstein lineage verify r-2026-05-05
```

The chain walks output → producing prompt → input artefact → upstream
producer recursively. CLI text output prints the most recent producer
first; `--full` walks to the leaves.

## Programmatic access

```python
from bernstein.core.persistence.lineage import LineageReader

reader = LineageReader(sdd_dir=".sdd")
for record in reader.iter_records(path="src/foo.py", line=42):
    print(record.producer.agent_id, record.prompt_sha, record.cost_usd)
```

Each `LineageRecord` carries:

- `output_artifact` — `path`, `sha256`, byte / line range
- `inputs` — list of `ArtifactRef`
- `producer` — `agent_id`, `run_id`, `tick_id`
- `prompt_sha`, `model`, `cost_usd`, `tokens`, `timestamp`
- `regulatory_class`, `customer_signature` (schema v2; null for v1
  records)

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `lineage.enabled` | `true` | Emit records on every write. |
| `lineage.compaction.enabled` | `true` | Janitor gzips per-day files at compaction time. |
| `lineage.regulatory_class.default` | `null` | Pin a default class for the run (Phase 2 / regulator-class). |
| `lineage.customer_signing.*` | see [regulator doc](../compliance/regulatory-lineage.md) | Customer-key signing (Phase 2). |

`bernstein debug bundle` includes the lineage graph for the run.

## Limitations

- Single-run today. Cross-run stitching across multiple `bernstein
  run` invocations is a follow-up.
- No backfill. Historical writes from before the feature shipped have
  no records.
- No GUI. CLI text and the HTML exporter only.
- PII redaction lives in `core/security/pii_gating.py`; lineage
  records inherit whatever redaction the audit log already applies —
  no extra layer.

## Related

- Source: `src/bernstein/core/persistence/lineage.py`
- CLI: `src/bernstein/cli/commands/lineage_cmd.py`,
  `lineage_export_cmd.py`, `lineage_verify_cmd.py`
- [Regulator-class lineage](../compliance/regulatory-lineage.md) — schema-v2 add-ons (regulatory class, customer signature, tamper-loud surface)
- PRs #996, #1013, #1017; tickets `2026-05-05-feat-artifact-lineage-trail.md`, `2026-05-05-feat-regulatory-lineage.md`
