# Module Decomposition Verification Checklist

Generated: 2026-04-12

## Baseline Test Results (pre-decomposition)

- **Test files**: 888 passed, 0 failed
- **Individual tests**: ~1617 passed (across 888 files)
- **Runner**: `uv run python scripts/run_tests.py -x --parallel 4`
- **Wall time**: 686.7s (4 workers)

## Current Module Sizes (pre-decomposition)

| Module | Lines |
|--------|-------|
| `core/orchestrator.py` | 4198 |
| `core/spawner.py` | 2914 |
| `core/task_lifecycle.py` | 2548 |
| `cli/dashboard.py` | 2510 |
| `core/gate_runner.py` | 2035 |
| `cli/run_cmd.py` | 1973 |
| `core/seed.py` | 1951 |
| `core/routes/tasks.py` | 1895 |
| `core/task_store.py` | 1853 |
| `core/agent_lifecycle.py` | 1776 |
| `core/server.py` | 1747 |
| `core/router.py` | 1557 |
| `core/routes/status.py` | 1510 |
| `tui/widgets.py` | 1757 |

## Import Catalog: Symbols That Must Be Preserved

Each module below must continue to export every listed symbol after decomposition.
The original module file becomes a re-export shim that imports from sub-modules.

### `bernstein.core.orchestrator` (12 symbols)

- `Orchestrator`
- `OrchestratorConfig`
- `TickResult`
- `group_by_role`
- `ShutdownInProgress`
- `_build_container_config`
- `_compute_total_spent`
- `_total_spent_cache`
- `_build_notification_manager`
- `get_orchestrator_nudges` (re-export from nudge_manager)
- `nudge_manager` (re-export from nudge_manager)

### `bernstein.core.spawner` (12 symbols)

- `AgentSpawner`
- `_extract_tags_from_tasks`
- `_render_prompt`
- `_render_fallback`
- `_render_batch_prompt`
- `_select_batch_config`
- `_load_role_config`
- `_health_check_interval`
- `_inject_scheduled_tasks`
- `build_tool_allowlist_env`
- `check_tool_allowed`
- `parse_tool_allowlist_env`

### `bernstein.core.task_lifecycle` (15 symbols)

- `process_completed_tasks`
- `claim_and_spawn_batches`
- `maybe_retry_task`
- `retry_or_fail_task`
- `should_auto_decompose`
- `auto_decompose_task`
- `collect_completion_data`
- `check_file_overlap`
- `infer_affected_paths`
- `_get_active_agent_files`
- `_enqueue_paired_test_task`
- `_move_backlog_ticket`
- `prepare_speculative_warm_pool`
- `create_conflict_resolution_task`
- `deprioritize_old_unclaimed_tasks`

### `bernstein.cli.dashboard` (18 symbols)

- `BernsteinApp`
- `AgentWidget`
- `ExpertBanditPanel`
- `DashboardHeader`
- `AgentListContainer`
- `_AGENT_WIDGET_HEIGHT`
- `_MAX_VISIBLE_AGENTS`
- `_build_runtime_subtitle`
- `_format_gate_report_lines`
- `_format_relative_age`
- `_gate_status_color`
- `_mini_cost_sparkline`
- `_summarize_agent_errors`
- `_task_retry_count`
- `_format_activity_line`
- `_gradient_text`
- `_priority_cell`
- `_role_glyph`

### `bernstein.core.gate_runner` (9 symbols)

- `GateRunner`
- `GateReport`
- `GateResult`
- `GatePipelineStep`
- `VALID_GATE_NAMES`
- `normalize_gate_condition`
- `build_default_pipeline`
- `_is_dep_file`
- `_migration_downgrade_is_pass`

Note: `GateCheckResult` is only referenced in the cookiecutter template, not in runtime code.

### `bernstein.core.router` (15 symbols)

- `TierAwareRouter`
- `ProviderConfig`
- `ProviderHealthStatus`
- `Tier`
- `ModelConfig`
- `ModelPolicy`
- `PolicyFilter`
- `RouterError`
- `RouterState`
- `route_task`
- `auto_route_task`
- `get_default_router`
- `load_model_policy_from_yaml`
- `load_providers_from_yaml`
- `signal_max_tokens_escalation`

### `bernstein.core.task_store` (6 symbols)

- `TaskStore`
- `ArchiveRecord`
- `SnapshotEntry`
- `ProgressEntry`
- `PANEL_GRACE_MS`
- `_retry_io`

Note: `get_task_store` is only in `scripts/researcher_sandbox.sh` (shell heredoc), not Python src.

### `bernstein.core.agent_lifecycle` (20 symbols)

- `handle_orphaned_task`
- `check_stalled_tasks`
- `check_kill_signals`
- `check_loops_and_deadlocks`
- `check_stale_agents`
- `reap_dead_agents`
- `recycle_idle_agents`
- `refresh_agent_states`
- `send_shutdown_signals`
- `purge_dead_agents`
- `classify_agent_abort_reason`
- `_has_git_commits_on_branch`
- `_maybe_preserve_worktree`
- `_save_partial_work`
- `_try_compact_and_retry`
- `_IDLE_GRACE_S`
- `_IDLE_HEARTBEAT_THRESHOLD_S`
- `_IDLE_HEARTBEAT_THRESHOLD_EVOLVE_S`
- `_COMPACT_MAX_RETRIES`
- `_COMPACT_RETRY_META`

### `bernstein.core.server` (51 symbols)

- `create_app`
- `SSEBus`
- `TaskStore`
- `TaskCreate`
- `TaskResponse`
- `TaskCompleteRequest`
- `TaskFailRequest`
- `TaskProgressRequest`
- `TaskPatchRequest`
- `TaskBlockRequest`
- `TaskCancelRequest`
- `TaskSelfCreate`
- `TaskStealAction`
- `TaskStealRequest`
- `TaskStealResponse`
- `TaskWaitForSubtasksRequest`
- `TaskCountsResponse`
- `HeartbeatRequest`
- `HeartbeatResponse`
- `StatusResponse`
- `HealthResponse`
- `RoleCounts`
- `PaginatedTasksResponse`
- `BatchClaimRequest`
- `BatchClaimResponse`
- `BatchCreateRequest`
- `BatchCreateResponse`
- `BulletinPostRequest`
- `BulletinMessageResponse`
- `ClusterStatusResponse`
- `NodeRegisterRequest`
- `NodeHeartbeatRequest`
- `NodeResponse`
- `PartialMergeRequest`
- `PartialMergeResponse`
- `AgentKillResponse`
- `AgentLogsResponse`
- `ChannelQueryRequest`
- `ChannelQueryResponse`
- `ChannelResponseRequest`
- `ChannelResponseResponse`
- `WebhookTaskCreate`
- `WebhookTaskResponse`
- `A2AAgentCardResponse`
- `A2AArtifactRequest`
- `A2AArtifactResponse`
- `A2AMessageRequest`
- `A2AMessageResponse`
- `A2ATaskResponse`
- `A2ATaskSendRequest`
- `DEFAULT_JSONL_PATH`
- `read_log_tail`
- `task_to_response`
- `node_to_response`
- `a2a_message_to_response`
- `a2a_task_to_response`
- `_sse_heartbeat_loop`

Note: `_parse_upgrade_dict` was moved to task_store; `store_postgres.py` references it with `type: ignore`.

### `bernstein.core.seed` (17 symbols)

- `parse_seed`
- `SeedConfig`
- `SeedError`
- `NotifyConfig`
- `CORSConfig`
- `MetricSchema`
- `NetworkConfig`
- `RateLimitBucketConfig`
- `RateLimitConfig`
- `StorageConfig`
- `seed_to_initial_task`
- `_build_manager_description`
- `_parse_cors_config`
- `_parse_dashboard_auth`
- `VALID_GATE_NAMES` (re-export from gate_runner)
- `GatePipelineStep` (re-export from gate_runner)
- `normalize_gate_condition` (re-export from gate_runner)

### `bernstein.cli.run_cmd` (16 symbols)

- `run`
- `cook`
- `demo`
- `init`
- `start`
- `detect_available_adapter`
- `setup_demo_project`
- `DEMO_TASKS`
- `exec_restart`
- `RunCostEstimate`
- `_emit_preflight_runtime_warnings`
- `_estimate_run_preview`
- `_finalize_run_output`
- `_wait_for_run_completion`
- `_show_dry_run_plan`
- `_generate_default_yaml`

### `bernstein.tui.widgets` (33 symbols)

- `TaskRow`
- `TaskListWidget`
- `AgentLogWidget`
- `ActionBar`
- `StatusBar`
- `ShortcutsFooter`
- `CoordinatorDashboard`
- `CoordinatorRow`
- `ApprovalEntry`
- `ApprovalPanel`
- `ScratchpadViewer`
- `ScratchpadEntry`
- `ToolObserverWidget`
- `WaterfallWidget`
- `QualityGateResult`
- `ModelTierEntry`
- `SLOBurnDownWidget`
- `STATUS_COLORS`
- `STATUS_DOTS`
- `SPARKLINE_CHARS`
- `status_color`
- `status_dot`
- `agent_badge_color`
- `classify_role`
- `build_coordinator_summary`
- `build_token_budget_bar`
- `build_cache_hit_sparkline`
- `list_scratchpad_files`
- `filter_scratchpad_entries`
- `build_model_tier_entries`
- `render_model_tier_table`
- `render_waterfall_batches`
- `build_slo_burndown_text`

### `bernstein.core.routes.tasks` (1 symbol)

- `router`

### `bernstein.core.routes.status` (2 symbols)

- `router`
- `build_alerts`

## Verification Commands (post-merge)

Run these in order after all decomposition branches are merged to `main`:

```bash
# 1. Symbol import verification + line-count + cycle check
uv run python scripts/verify_decomposition.py

# 2. Full test suite (baseline: 888 files, ~1617 tests, 0 failures)
uv run python scripts/run_tests.py -x --parallel 4

# 3. Type checking
uv run pyright src/bernstein/

# 4. Lint
uv run ruff check src/bernstein/
```

## Acceptance Criteria

1. `verify_decomposition.py` exits 0 (all 232 symbols importable, all files <= 800 lines, no cycles)
2. Test suite: >= 888 files pass, 0 failures (new sub-module tests are a bonus)
3. Pyright: no new errors vs. baseline
4. Ruff: clean (no new violations)
5. Each of the 14 original module paths still works as an import target (re-export shim)
