# Recent Decisions

No decisions recorded yet.

## [2026-03-28 18:50] [RETRY 2] [RETRY 1] 738 — Reduce Agent Failure Rate (18% → <5%) (9792a50e0f1a)
Completed: [RETRY 2] [RETRY 1] 738 — Reduce Agent Failure Rate (18% → <5%). Implemented: (1) Auto-decompose for large/repeatedly-failed tasks (already existed, verified), (2) File context discovery from task keywords when owned_files empty (new - TaskContextBuilder._discover_relevant_files), (3) Complexity-aware routing: opus/max for large/architect/security (verified in both _select_batch_config and _retry_or_fail_task), (4) Progressive timeout in _maybe_retry_task (new - was missing), (5) High-stakes escalation in _maybe_retry_task (new - opus/max for large/architect/security on any retry). All 280 tests pass (171 orchestrator + 51 spawner + 39 context + 19 failure_reduction).

## [2026-03-28 18:51] 704 — Aider CLI Adapter (8a0b2f730837)
Completed: 704 — Aider CLI Adapter

## [2026-03-28 18:52] [RETRY 2] 738 — Reduce Agent Failure Rate (18% → <5%) (72641df14973)
Completed: [RETRY 2] 738 — Reduce Agent Failure Rate (18% → <5%). All 5 fixes verified: (1) auto-decompose for large/repeatedly-failed tasks via _should_auto_decompose + _auto_decompose_task, (2) file context injection via TaskContextBuilder._discover_relevant_files, (3) complexity-aware routing with opus/max for large/architect/security in _select_batch_config, (4) progressive timeout in _retry_or_fail_task and _maybe_retry_task, (5) pre-flight validation via auto-decompose check in _claim_and_spawn_batches. 190 tests passing (19 in test_failure_reduction.py + 171 in test_orchestrator.py).

## [2026-03-28 18:53] 708 — Interactive Session Streaming (Crystal-killer) (fee1a7d12306)
Completed: 708 — Interactive Session Streaming (Crystal-killer)

## [2026-03-28 18:54] 707 — Bernstein as MCP Server (9afa5b33d4e3)
Completed: 707 — Bernstein as MCP Server

## [2026-03-28 18:55] 703 — Slack/Discord/Telegram Notifications (58d46212d599)
Completed: 703 — Slack/Discord/Telegram Notifications

## [2026-03-28 18:58] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT) (0a8efb1dbe6c)
Completed: 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT)

## [2026-03-28 18:58] 711 — Public Web Dashboard Demo Instance (3dd3261f99ba)
Completed: 711 — Public Web Dashboard Demo Instance

## [2026-03-28 19:01] 726 — SYNAPSE-Inspired Adaptive Governance for Evolution (9f164cc858e0)
Completed: 726 — SYNAPSE-Inspired Adaptive Governance for Evolution

## [2026-03-28 19:01] 735 — Agents Create PRs Instead of Direct Push (b6bf71174f64)
Completed: 735 — Agents Create PRs Instead of Direct Push

## [2026-03-28 19:03] [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT) (d63089acf939)
Completed: [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT)

## [2026-03-28 19:03] [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT) (39f8b3e7cfbd)
Completed: [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT)

## [2026-03-28 19:04] 740 — Team Coordination Patterns (eced2b0f972b)
Completed: 740 — Team Coordination Patterns

## [2026-03-28 19:05] [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT) (e5681f9d06df)
Completed: [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT)

## [2026-03-28 19:05] [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT) (1162e6cf24f8)
Completed: [RETRY 1] 736 — Agent Signal Files (WAKEUP / SHUTDOWN / HEARTBEAT)
