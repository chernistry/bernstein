# Incident-to-eval synthesis

Bernstein already captures incidents in three places: the dead-letter
queue, orchestrator postmortems, and the flaky-test detector. Until
this feature, those records stayed where they were created and never
became a **gate** on future runs. The same prompt-injection /
token-runaway / adapter-timeout patterns surfaced as repeat incidents
weeks apart.

`incident_synthesizer` ingests each incident, redacts secrets,
extracts the smallest reproducible trigger, and emits a YAML eval
case. The eval corpus thus grows from production failures, and CI
runs them as gates: P0 cases block release, P1 / P2 warn.

## Why it exists

"Log and forget" was the failure mode. The fix is "every P0/P1
incident adds one regression case that future agents must pass."
That closes the loop.

## How to use it

Run on demand or on the `task_terminally_failed` lifecycle hook:

```bash
# Sync now: read every incident, emit YAML cases under
# src/bernstein/eval/cases/incidents/
bernstein eval sync-incidents

# Dry-run to see what would be generated without writing
bernstein eval sync-incidents --dry-run
```

The synthesiser:

1. Reads dead-letter queue + postmortem artefacts.
2. Strips secrets via `core/security/sanitize.py`.
3. Extracts the smallest trigger — the failing prompt, failing config,
   failing tool-call sequence.
4. Writes one YAML per incident, idempotent by content hash.

Sample emitted case:

```yaml
id: inc-prompt-injection-2026-04-22
severity: P0
prompt: |
  <minimal trigger that surfaced the original failure>
expected_outcome:
  - "agent refuses to follow injected instruction"
  - "audit log carries DECISION_DENIED"
source_incident: postmortem-2026-04-22-T14:33:11Z
```

The quality-gate pipeline runs every incident-derived case alongside
the rest of the eval suite. Failures on P0 cases are blocking; P1 /
P2 print warnings.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `eval.incident_sync.on_terminal_failure` | `true` | Auto-sync on every dead-letter event. |
| `eval.incident_sync.write_path` | `src/bernstein/eval/cases/incidents/` | Where the YAML cases live. |
| `eval.gate_severity_blocking` | `["P0"]` | Which severities block merge. |

Metrics:

- `bernstein_incident_evals_total{severity}`
- `bernstein_incident_recurrence_rate`

## Limitations

- No LLM-driven fuzz expansion (one incident = one case).
- No corpus auto-pruning — old cases accumulate until manually
  trimmed; tracked as a follow-up.
- Cross-tenant incident sharing is out of scope.
- The minimaliser extracts the trigger using deterministic rules; it
  does not understand semantic intent. For unusual incident shapes
  the case may need hand-editing.

## Related

- Source: `src/bernstein/eval/incident_synthesizer.py`
- Inputs: `core/tasks/dead_letter_queue.py`,
  `core/observability/postmortem.py`
- Quality gate: `core/quality/gate_pipeline.py`
- CLI: `bernstein eval sync-incidents`
- PR #1001, ticket `2026-04-30-feat-incident-to-eval-synthesis.md`
