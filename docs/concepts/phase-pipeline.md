# Discrete phase pipeline

A plan step can split into discrete `research → plan → implement →
verify` phases, each spawned as a fresh short-lived agent with its own
context window. Between phases, only the **distilled handoff** —
summary, decisions, constraints, open questions — passes forward. The
implement phase never sees the research transcript.

## Why it exists

When one long-running agent does research, planning, and execution in
the same window, it burns 60 k tokens reading the codebase, emits a
plan, then keeps reading on top of that bloat while implementing —
hitting compaction and degrading quality. Discrete phases keep each
context window small and let the router pick a different model per
phase: a high-reasoning model for research, a cheaper one for
implementation.

## How to use it

Add a `phases:` list to a step:

```yaml
stages:
  - name: feature
    steps:
      - role: backend
        goal: "Add streaming responses to the chat handler"
        phases: [research, plan, implement, verify]
```

When the orchestrator reaches that step, it spawns one agent per
phase. Each phase writes a structured artefact to
`.sdd/runtime/phase_artifacts/<task_id>/<phase>.json`:

```json
{
  "summary":         "...",
  "decisions":       ["..."],
  "constraints":     ["..."],
  "open_questions":  ["..."]
}
```

The next phase's prompt is seeded with that JSON only — not the prior
phase's transcript.

Existing single-phase plans (no `phases:` field) run unchanged.

## Routing per phase

`core/routing/router.py` consults the phase to pick a model:

| Phase | Default model class | Why |
|---|---|---|
| `research` | high-reasoning (sonnet / opus tier) | broad codebase reading, gap analysis |
| `plan` | high-reasoning | cross-cutting design |
| `implement` | cheap-tier | bounded, high-throughput edits |
| `verify` | cheap-tier | structured assertion runner |

Override per task via the existing `model:` field on the step.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `phase_pipeline.enabled` | `true` | Honour `phases:` in plans. |
| `phase_pipeline.artefact_path` | `.sdd/runtime/phase_artifacts/` | Distilled handoff store. |
| `phase_pipeline.gc_on_close` | `true` | Drop artefacts when the parent task closes. |

## Limitations

- One level of phasing per task. No nested phases inside a phase.
- The handoff schema is a fixed dataclass; no LLM-based summariser
  filling in narrative prose.
- Mid-phase abort uses the existing `task_retry.py` path; there is no
  phase-specific restart granularity.
- Cross-task phase pooling (sharing a research artefact across two
  unrelated tasks) is not supported.

## Related

- Source: `src/bernstein/core/orchestration/phase_pipeline.py`
- Plan loader: `src/bernstein/core/planning/plan_loader.py`
- Routing: `src/bernstein/core/routing/router.py`
- PR #1000, ticket `2026-04-30-feat-discrete-phase-separation.md`
