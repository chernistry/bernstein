# Recent Decisions

No decisions recorded yet.

## [2026-03-28 22:10] 347 — Output Guardrails: Secret Detection + Scope Enforcement (7e2df2c42c20)
Completed: 347 — Output Guardrails: Secret Detection + Scope Enforcement

## [2026-03-28 22:10] 349 — Agents Create PRs Instead of Direct Push (857d1e7ed7fc)
Completed: 349 — Agents Create PRs Instead of Direct Push

## [2026-03-28 22:11] 335 — Crash Recovery / Orphan Agent Resume (24c98ad369ac)
Completed: 335 — Crash Recovery / Orphan Agent Resume

## [2026-03-28 22:11] 353 — Team Coordination Patterns (5141aecb08e9)
Completed: 353 — Team Coordination Patterns

## [2026-03-28 22:12] 352 — Agency Deep Integration: Specialist Prompts + Capabilities (76f7f4102b6b)
Completed: 352 — Agency Deep Integration: Specialist Prompts + Capabilities

## [2026-03-28 22:12] 358 — Apple-like UX overhaul: zero-friction first run, progressive disclosure (6549c3a6e93c)
Completed: 358 — Apple-like UX overhaul: zero-friction first run, progressive disclosure

## [2026-03-28 22:13] 369 — SYNAPSE-Inspired Adaptive Governance for Evolution (7bcb94aba934)
Completed: 369 — SYNAPSE-Inspired Adaptive Governance for Evolution

## [2026-03-28 22:13] 359 — Fix critical UX blockers: broken aliases, missing pre-flight checks (01570eb8ba8a)
Completed: 359 — Fix critical UX blockers: broken aliases, missing pre-flight checks

## [2026-03-28 22:13] 365 — Public Web Dashboard Demo Instance (3ed973b23808)
Completed: 365 — Public Web Dashboard Demo Instance. docker/demo/ infrastructure was already in place (Dockerfile, docker-compose.yaml, Caddyfile, demo-cycle.sh, .env.demo.example). Added: (1) Caddyfile now exposes /agents/* to the public for live output streaming. (2) Dashboard Live Output section wired to per-agent SSE streams via /agents/{id}/stream — auto-opens EventSource connections as agents appear. (3) Janitor saves git diffs to .sdd/diffs/{task_id}.diff on task completion; new GET /tasks/{id}/diff route serves them; dashboard shows colorized diff modal when a done task row is clicked. All 105 server tests + 61 janitor tests pass.

## [2026-03-28 22:15] 338 — HN Show Launch Package (be925291f382)
Completed: 338 — HN Show Launch Package

## [2026-03-28 22:15] 339 — Technical Content Strategy (232b87df5427)
Completed: 339 — Technical Content Strategy. All 5+ content pieces already drafted: 2 Twitter threads (47-seconds, deterministic-orchestration), architecture blog post (zero-llm-coordination), Reddit post (reddit-local-llama), Show HN post (hn-post), YouTube demo script, multi-agent benchmark blog post. Content calendar updated to include all pieces.

## [2026-03-28 22:16] 340 — Benchmark vs GitHub Agent HQ (10fd530a2754)
Completed: 340 — Benchmark vs GitHub Agent HQ

## [2026-03-28 22:22] 365 — Public Web Dashboard Demo Instance (1390a6007b33)
Completed: 365 — Public Web Dashboard Demo Instance

## [2026-03-28 22:24] [RETRY 1] 333d — Smart Task Distribution (No Greedy Claiming) (cd10e151ce67)
Completed: [RETRY 1] 333d — Smart Task Distribution (No Greedy Claiming). Implemented round-robin interleaving in group_by_role (tick_pipeline.py) and per-role cap enforcement in claim_and_spawn_batches (task_lifecycle.py). Batches now interleave across roles so no role hogs agent slots. Cap formula: ceil(max_agents * role_tasks / total_tasks). All 174 tests pass, ruff+pyright clean.

## [2026-03-28 22:25] [RETRY 1] 333g — Idle Agent Detection and Recycling (60d4927c02b1)
Completed: [RETRY 1] 333g — Idle Agent Detection and Recycling
