# Best-of-N delegation

For tasks tagged "complex" or "ambiguous", Bernstein can spawn K
parallel candidate agents in isolated worktrees, score each candidate
with automated signals (tests pass, lint clean, diff size) plus an
LLM-as-judge rubric, and merge only the winner. Losing candidates'
worktrees are cleaned up automatically.

## Why it exists

`task_retry` retries serially: fail → escalate → retry. That works
for transient errors. It does **not** work for genuinely ambiguous
tasks where serial retries compound the same wrong assumption. For
those, K parallel attempts in isolated sandboxes plus a judge is
cheaper in wall-clock and tokens than serial retries.

The infrastructure to spawn parallel candidates already existed (git
worktrees, sandbox backends, adaptive parallelism). This module is
the candidate-selection layer on top.

## How to use it

Mark a task with `best_of_n` in the plan or via the API:

```yaml
stages:
  - name: refactor
    steps:
      - role: backend
        goal: "Migrate the auth module from Flask to FastAPI"
        best_of_n: 3
```

Or programmatically:

```python
task = Task(
    id="...",
    goal="Migrate the auth module from Flask to FastAPI",
    best_of_n=3,
)
```

When the orchestrator picks up a task with `best_of_n=K`, it:

1. Spawns K agents into K isolated worktrees.
2. Awaits all K to finish (or hit the per-candidate timeout).
3. Computes `score_candidate(result)` for each — weighted sum of
   tests-passing, lint-score, diff size, runtime.
4. Asks a cheap-tier LLM judge to rank candidates against a rubric.
5. Merges the highest combined score; deletes the other worktrees.

The cross-model verifier still runs **on the winner** before merge.
Best-of-N does not replace verification — it picks a candidate to
verify.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `defaults.BEST_OF_N_DEFAULT` | `1` (off) | Default `best_of_n` for tasks that don't set one. |
| `defaults.BEST_OF_N_MAX` | `5` | Hard cap, regardless of what a plan asks for. |
| `defaults.BEST_OF_N_JUDGE_RUBRIC_PATH` | `templates/prompts/best_of_n_judge.md` | Rubric the LLM judge uses. |

Metrics:

- `best_of_n_judge_score` (histogram)
- `best_of_n_candidates_total{outcome}` — `winner` / `loser` /
  `error`.

## Limitations

- One level of branching per task. No nested best-of-N inside a
  candidate.
- Auto-decision of when to escalate K (e.g., raise to 5 if confidence
  is low) is not in v1 — set `best_of_n` manually.
- All candidates run the same model unless you also set per-candidate
  `mode_profile` overrides.
- K worktrees mean K times the disk and parallel agent budget; the
  existing `adaptive_parallelism` cap still applies.

## Related

- Source: `src/bernstein/core/orchestration/best_of_n.py`
- Tick pipeline: `src/bernstein/core/orchestration/tick_pipeline.py`
- [Adaptive parallelism](../architecture/adaptive-parallelism.md)
- [Quality Pipeline](../architecture/quality-pipeline.md)
- PR #1011, ticket `2026-04-30-feat-best-of-n-delegation.md`
