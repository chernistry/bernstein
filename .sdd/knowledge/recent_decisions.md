# Recent Decisions

No decisions recorded yet.

## [2026-03-29 00:33] Plan and decompose goal into tasks (8e0c499c6977)
Completed: Plan and decompose goal into tasks. Goal was "test" — no actionable subtasks to create.

## [2026-03-29 00:34] [373c] LLM Judge Implementation (a19f2e757fa1)
Completed: [373c] LLM Judge Implementation. EvalJudge class with dual_attempt, circuit_breaker, retry_with_backoff, review_git_diff methods. CircuitBreakerTripped exception. 44 tests pass.

## [2026-03-29 00:34] [DECOMPOSE] [RETRY 2] [RETRY 1] 333d-03: Agent exit logic for rebalancing (a402f33a9ad9)
Completed: [DECOMPOSE] [RETRY 2] [RETRY 1] 333d-03: Agent exit logic for rebalancing. Decomposed into 4 subtasks: 333d-03a (spawn prevention verify), 333d-03b (integration test), 333d-03c (no orphans test), 333d-03d (rebalancing test).

## [2026-03-29 00:35] [373e] CLI Integration & Reporting (356db9bba6bd)
Completed: [373e] CLI Integration & Reporting. Eval command group already wired into CLI with eval run (--tier, --compare, --save), eval report, and eval failures subcommands. All 52 tests pass.

## [2026-03-29 00:37] 382-01: git_ops.py foundation — branching, safe_push, staging (7317d221f067)
Completed: 382-01: git_ops.py foundation — branching, safe_push, staging. Added create_branch(branch_name, base="main") and delete_old_branches(older_than_hours=24) with remote cleanup support. safe_push and stage_task_files were already implemented. All 86 tests pass.

## [2026-03-29 00:37] [RETRY 2] [RETRY 1] 333d-03: Agent exit logic for rebalancing (4badf2ea6dca)
Completed: [RETRY 2] [RETRY 1] 333d-03: Agent exit logic for rebalancing. Added Case 4 to recycle_idle_agents: role_drained_rebalance triggers SHUTDOWN when a role has zero active tasks (open+claimed+in_progress), catching orphaned task_ids that Cases 1-3 miss. Spawn suppression already exists via per-role cap in claim_and_spawn_batches. 5 new tests + all 18 idle recycling tests + 206 orchestrator tests pass.

## [2026-03-29 00:39] 382-02: git_ops.py — conventional commits and bisect (3e078d3598e5)
Completed: 382-02: git_ops.py — conventional commits and bisect. conventional_commit() generates deterministic conventional commit messages from staged diffs (type/scope detection, diff-stat bullets, Refs+Co-Authored-By footer). bisect_regression() wraps git bisect run to find first bad commit. 86/86 tests pass including 20 tests for these functions and helpers.

## [2026-03-29 00:40] 382-03: git_context.py — git intelligence for agents (559b356a859a)
Completed: 382-03: git_context.py — git intelligence for agents. All 4 core functions (blame_summary, hot_files, cochange_files, recent_changes) plus helpers (ls_files, ls_files_pattern, recent_changes_multi, build_agent_git_context, _epoch_to_relative) implemented and tested. 28/28 tests pass.

## [2026-03-29 00:40] [RETRY 1] 333d-03c: Verify no orphaned tasks when agent exits via role queue empty (2614b43aedcc)
Completed: [RETRY 1] 333d-03c: Verify no orphaned tasks when agent exits via role queue empty

## [2026-03-29 00:42] 382-04: Migration — replace git subprocess calls (0b44656cbc8f)
Completed: 382-04: Migration — replace git subprocess calls. All three target files (orchestrator.py, context.py, spawner.py) already use centralized git_ops/git_context — zero subprocess.run(["git",...]) calls found. 86/86 git_ops tests pass.

## [2026-03-29 00:42] 333d-03b: Integration test - agent exits when role queue empties (8c959d2d9bf8)
Completed: 333d-03b: Integration test - agent exits when role queue empties

## [2026-03-29 00:43] 340b — VS Code / Cursor Extension (27cb4038a5a6)
Completed: 340b — VS Code / Cursor Extension. TypeScript extension with agent/task tree views, status bar, SSE real-time updates, @bernstein chat participant, webview dashboard sidebar. 23/23 tests pass. bernstein-0.1.0.vsix packages clean. CI/CD for VS Code Marketplace + Open VSX.

## [2026-03-29 00:43] 333d-03d: Test agent rebalancing prevents idle accumulation (e68257d28588)
Completed: 333d-03d: Test agent rebalancing prevents idle accumulation. Created test_rebalancing.py with 5 comprehensive tests covering task completion recycling, spawn prevention, graceful exit with grace period, active agent preservation, and empty role handling.

## [2026-03-29 00:44] [DECOMPOSE] Plan and decompose goal into tasks (3a7b97022015)
Completed: [DECOMPOSE] Plan and decompose goal into tasks. Original task goal was 'test' (empty/non-actionable). No subtasks to create.

## [2026-03-29 00:44] [RETRY 1] 333d-03a: Verify spawn prevention guards in claim_and_spawn_batches (bfcd70e7ef74)
Completed: [RETRY 1] 333d-03a: Verify spawn prevention guards in claim_and_spawn_batches. Verified that _alive_per_role at lines 592-595 already correctly excludes idle agents (in _idle_shutdown_ts) from the count, so _effective_role_cap prevents spawning only when non-idle agents >= batches. No code changes needed. All related tests pass: TestAgentRebalancing::test_spawn_allowed_when_idle_agent_waiting_to_exit, TestPerRoleCapDistribution (5 tests), TestIdleAgentRecycling (19 tests).
