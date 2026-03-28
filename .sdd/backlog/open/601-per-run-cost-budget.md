# 601 — Per-Run Cost Budget

**Role:** backend
**Priority:** 1 (critical)
**Scope:** medium
**Depends on:** none

## Problem

There is no mechanism to limit spending on a single orchestration run. 85% of enterprises misestimate AI agent costs. Runaway agents can burn through API credits with no guardrails. No competitor currently offers hard dollar-limit enforcement.

## Design

Implement a per-run cost budget system with three components: real-time token/cost tracking per agent, configurable hard dollar limits per run, and automatic model fallback when approaching the cap. Each agent reports token usage back to the orchestrator after every model call. The orchestrator maintains a running cost total using a provider-specific pricing table. When cost reaches configurable thresholds (e.g., 80%, 95%), emit warnings and trigger model downgrades. At 100%, hard-stop remaining agents. Store cost data in `.sdd/runtime/costs/`. Expose cost info via the task server API (`GET /costs/{run_id}`). Configuration lives in `.sdd/config.toml` under `[budget]`.

## Files to modify

- `src/bernstein/core/orchestrator.py`
- `src/bernstein/core/spawner.py`
- `src/bernstein/core/cost_tracker.py` (new)
- `src/bernstein/core/pricing.py` (new)
- `src/bernstein/core/task_server.py`
- `.sdd/config.toml`
- `tests/unit/test_cost_tracker.py` (new)

## Completion signal

- `bernstein run` with `--budget 5.00` stops agents when $5 is reached
- Cost data visible via `GET /costs/{run_id}` endpoint
- Unit tests pass for tracking, threshold warnings, and hard stops
