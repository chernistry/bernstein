# Action cache and replay

The WAL gives Bernstein crash recovery. The **action cache** sits one
layer above it and gives Bernstein **deterministic replay without
paying the LLM bill**. Every action — prompt, model output, tool
call, tool result — is content-addressed by `(model_id,
normalized_prompt, tool_name, tool_args)` and stored under
`.sdd/runtime/action_cache/<sha256>.json`. On replay, cache hits
return the recorded result; misses fall through to the live model and
append.

## Why it exists

Two scenarios drove this:

1. **CI re-runs.** The same self-evolving smoke test runs on every PR.
   Without a cache, each run pays full LLM cost.
2. **Regression bisecting.** Verifying that a fix doesn't break a
   known-good path is impossible if every re-run costs money and
   produces non-deterministic output.

The cache also produces a deterministic golden record we diff against
to catch silent agent-output drift between model versions.

## How to use it

Pick a mode and run:

```bash
# Record (default in normal runs once enabled)
bernstein run plan.yaml --cache record

# Replay-only — fail-loud on cache miss instead of calling the model
bernstein run plan.yaml --cache replay

# Hybrid — replay on hit, fall through to live model on miss, append result
bernstein run plan.yaml --cache hybrid

# Re-execute a past run against its cache; emit a diff report on drift
bernstein replay <run_id>

# Inspect / prune the cache
bernstein cache action ls
bernstein cache action prune --older-than 30d
```

The `replay` subcommand walks the run's recorded actions, executes
each against the cache, and reports any divergence between recorded
and live output. Useful for catching model-version drift.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `cache.action_cache.enabled` | `true` | Master switch. |
| `cache.action_cache.mode` | `record` | `record` / `replay` / `hybrid`. |
| `cache.action_cache.size_mb` | `500` | LRU eviction cap. |

Metrics:

- `bernstein_action_cache_hits_total{site}`
- `bernstein_action_cache_savings_usd` — estimated token-cost saved by
  replay hits.

## Limitations

- Exact-key only. No semantic-similarity match across "almost the
  same" prompts.
- Bash / exec tool calls have side effects we cannot replay (file
  writes, network calls). The cache covers the LLM and read-only tool
  layer; bash is recorded but not replayed.
- Single host. Cross-machine cache sharing rides on
  `core/storage/sink.py` plumbing if you need it; not in v1.
- Replay is byte-comparison strict. A different timestamp in a
  recorded log is a "drift" finding even if functionally equivalent.

## Related

- Source: `src/bernstein/core/persistence/action_cache.py`
- Layered on: `src/bernstein/core/persistence/fingerprint.py` (memo
  store)
- CLI: `src/bernstein/cli/commands/cache_cmd.py`,
  `replay_filter_cmd.py`
- PR #999, ticket `2026-04-30-feat-action-caching-replay.md`
