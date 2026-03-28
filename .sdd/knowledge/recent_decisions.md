# Recent Decisions

No decisions recorded yet.

## [2026-03-28 07:13] [RETRY 1] Add --dry-run flag to orchestrator that previews task plan without spawning agents (25507b48fd90)
Completed: [RETRY 1] Add --dry-run flag to orchestrator that previews task plan without spawning agents

## [2026-03-28 07:13] [RETRY 2] 413 -- GitHub Pages documentation site (77be6161ad92)
Completed: [RETRY 2] 413 -- GitHub Pages documentation site. All 6 files exist (index.html, getting-started.html, concepts.html, api.html, style.css, script.js). Total size 81KB (<100KB). All completion signals verified: Bernstein and viewport present in index.html.

## [2026-03-28 07:14] [RETRY 1] Fix all 31 ruff linting errors across src/bernstein/ (080dad2b57a6)
Completed: [RETRY 1] Fix all 31 ruff linting errors across src/bernstein/ — ruff check already returns 0 errors, all 1772 tests pass

## [2026-03-28 07:14] [RETRY 2] Fix all 31 ruff linting errors across src/bernstein/ (ba06002eacb6)
Completed: All ruff linting errors already fixed. Verified: 0 ruff errors, 1772 tests passing.

## [2026-03-28 07:14] [RETRY 1] Reduce HTTP round-trips in orchestrator tick loop (8184b626407a)
Completed: [RETRY 1] Reduce HTTP round-trips in orchestrator tick loop - refactoring was already applied in commit e900573: _fetch_all_tasks() fetches all tasks in one round-trip, tick passes tasks_by_status to all consumers, debug log counts HTTP reads per tick (should stay at 1).

## [2026-03-28 07:15] Evolve cycle 13: code quality (a86132e7fc17)
Cycle 13 (code quality): Created 4 tasks targeting dict[str,Any] soup (148 occurrences violating project standards) and broad exception handling (66 catches). Tasks: (1) TypedDicts in orchestrator.py, (2) narrow except Exception in orchestrator.py, (3) TypedDicts in server.py, (4) Task.from_dict classmethod to replace fragile manual deserialization.

## [2026-03-28 07:15] [RETRY 1] Lazy-import Rich dashboard components in CLI modules (05acde34d787)
Completed: [RETRY 1] Lazy-import Rich dashboard components in CLI modules

## [2026-03-28 07:15] [RETRY 1] Reduce HTTP round-trips in orchestrator tick loop (bd4b905f4e22)
Completed: [RETRY 1] Reduce HTTP round-trips in orchestrator tick loop - optimization already fully implemented: _fetch_all_tasks called once per tick, tasks_by_status passed to all consumers (_check_evolve, _reap_dead_agents, _refresh_agent_states), debug log counts HTTP reads per tick, fallback individual GETs only for edge cases.

## [2026-03-28 07:16] [RETRY 1] Reduce HTTP round-trips in orchestrator tick loop (daf62bcbc557)
Completed: [RETRY 1] Reduce HTTP round-trips in orchestrator tick loop. The optimization was already implemented: _fetch_all_tasks() is called once per tick, tasks_by_status dict is passed to all consumers (_check_evolve, _reap_dead_agents, _refresh_agent_states, _process_completed_tasks). _retry_or_fail_task accepts optional snapshot. Debug log counts HTTP reads per tick. HTTP reads per tick = 1.

## [2026-03-28 07:16] [RETRY 1] Lazy-import Rich dashboard components in CLI modules (2cbb63ecdb31)
Completed: [RETRY 1] Lazy-import Rich dashboard components in CLI modules

## [2026-03-28 07:19] Replace dict[str, Any] with TypedDicts in server.py (fcdd6a0bcb01)
Completed: Replace dict[str, Any] with TypedDicts in server.py

## [2026-03-28 07:20] Narrow except Exception catches in orchestrator.py (64252d0fd764)
Completed: All except Exception catches in orchestrator.py are already narrowed - OSError for KB refresh, CancelledError|RuntimeError for ruff/pytest futures, OSError|ValueError|RuntimeError for evolution cycle, Exception kept for top-level run loop guard.

## [2026-03-28 07:20] Move _task_from_dict into Task.from_dict classmethod (7aead781408e)
Completed: Move _task_from_dict into Task.from_dict classmethod

## [2026-03-28 07:20] [RETRY 1] Narrow except Exception catches in orchestrator.py (6fd32c193066)
Completed: [RETRY 1] Narrow except Exception catches in orchestrator.py

## [2026-03-28 07:22] Replace dict[str, Any] with TypedDicts in orchestrator.py (2baaf513f669)
Completed: Replace dict[str, Any] with TypedDicts in orchestrator.py
