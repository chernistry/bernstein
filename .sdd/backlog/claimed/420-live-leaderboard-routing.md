# 420 — Live Leaderboard-Driven Model Routing

**Role:** backend
**Priority:** 6 (future)
**Scope:** large
**Depends on:** none

## Problem

Model capabilities change fast — new models ship monthly, benchmarks shift, pricing changes. Bernstein's routing config is static (hardcoded SWE-bench scores from March 2026). Within 3 months it'll be stale. The router should pull fresh benchmark data and adjust routing automatically.

## Design

### Leaderboard fetcher
Periodically (weekly or on `bernstein agents sync`) fetch:
- SWE-bench Verified leaderboard (epoch.ai or swebench.com)
- Aider Polyglot leaderboard (aider.chat/docs/leaderboards)
- LiveBench coding scores
- Vendor pricing pages (or a community-maintained pricing JSON)

Parse into a normalized format:
```json
{"model": "claude-opus-4-6", "swe_bench": 80.8, "aider_poly": 82.1, "cost_per_1k": 0.015}
```

Store in `.sdd/config/leaderboard_cache.json` with TTL.

### Dynamic routing
The router reads leaderboard cache and picks models:
- Architect/security → highest SWE-bench score among available
- Backend → best score-per-dollar ratio
- QA/docs → cheapest that exceeds quality threshold (e.g. >65% SWE-bench)

### Fallback
If leaderboard fetch fails or cache is stale, use hardcoded defaults (current behavior).

## Files to modify

- `src/bernstein/core/leaderboard.py` (new — fetcher + parser)
- `src/bernstein/core/router.py` (read leaderboard cache)
- `src/bernstein/core/agent_discovery.py` (enrich with leaderboard data)

## Completion signal

- `bernstein agents sync` pulls fresh benchmark data
- Router adjusts model selection based on latest scores
- Stale cache degrades gracefully to hardcoded defaults
