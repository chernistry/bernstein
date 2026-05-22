# Deterministic LLM replay (hermetic by default)

Bernstein can record every LLM call of a run to
`.sdd/runs/<run_id>/llm_calls.jsonl` and replay those responses on a later
run, so a re-run produces an identical task decomposition without paying the
model bill. This page covers the orchestrator-wired `DeterministicStore`
path (selected with `BERNSTEIN_REPLAY_RUN_ID`).

## TL;DR

| Item | Behaviour |
|---|---|
| Replay default | Strict / hermetic. A cache miss aborts the run. |
| On a miss (strict) | Raises `ReplayMissError`; the live model is never called. |
| Escape hatch | `BERNSTEIN_REPLAY_ALLOW_LIVE_MISS=1` -> miss logs a WARNING and falls through to the live model. |
| Replay key | `(model, prompt, provider, temperature, max_tokens)`. Any drift is a miss, not a hit. |
| Coverage line | `hits` / `misses` / `strict_violations`; a fully covered replay reports `misses=0`. |

## How to record and replay

```bash
# Record: run with a deterministic seed; LLM calls are saved to
# .sdd/runs/<run_id>/llm_calls.jsonl
BERNSTEIN_DETERMINISTIC_SEED=42 bernstein run plan.yaml

# Replay: point at the recorded run. Replay is hermetic by default.
BERNSTEIN_REPLAY_RUN_ID=<run_id> bernstein run plan.yaml
```

A replay that matches every recorded call completes with zero live provider
calls and a coverage line whose `misses=0`.

## Strict mode (the default)

Replay is hermetic: if a prompt is not in the recording (a new prompt, a
reordered tool result, a changed model id, or a drifted
provider/temperature/max_tokens), `get_replay` raises `ReplayMissError`
instead of silently calling the live model. The run aborts. This guarantees a
run launched for replay is genuinely a replay - it cannot reach the network.

`ReplayMissError` subclasses `RuntimeError` and carries the prompt `key` and
`model`, and its message names exactly how to re-record.

## The replay key folds in every response-determining input

The lookup key is a SHA-256 over
`model \x00 prompt \x00 provider \x00 repr(temperature) \x00 max_tokens`. A
cache "hit" therefore cannot mask a parameter drift: the same `(model,
prompt)` recorded at `temperature=0.7` is a **miss** when replayed at
`temperature=0.0`.

### Re-recording note (behaviour change)

Folding provider/temperature/max_tokens into the key **invalidates
`llm_calls.jsonl` files recorded before this change** - older recordings used
a narrower `model \x00 prompt` key and now read as full misses under strict
replay. To re-record, run the original workload again with
`BERNSTEIN_DETERMINISTIC_SEED` set (and `BERNSTEIN_REPLAY_RUN_ID` unset). The
`ReplayMissError` message states this inline so an operator who hits a stale
recording knows the fix without leaving the log.

## Escape hatch (opt-in, non-hermetic)

For record-extend workflows you can keep the old fall-through behaviour:

```bash
BERNSTEIN_REPLAY_ALLOW_LIVE_MISS=1 BERNSTEIN_REPLAY_RUN_ID=<run_id> bernstein run plan.yaml
```

In this mode a miss emits a WARNING for each occurrence and then calls the
live provider. It is opt-in precisely so the hermetic guarantee stays closed
unless deliberately disabled; do not set it globally in CI or air-gapped
contexts.

## Related

- Source: `src/bernstein/core/orchestration/deterministic.py`
- Call site: `src/bernstein/core/routing/llm.py` (`call_llm`)
- Sibling subsystem with the same miss contract:
  `src/bernstein/core/replay/gateway.py` (`ReplayMissError`)
