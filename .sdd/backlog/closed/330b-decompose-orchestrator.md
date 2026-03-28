# 330b — Decompose orchestrator.py (3485 lines → modules)

**Role:** backend
**Priority:** 0 (urgent)
**Scope:** large
**Depends on:** none

## Problem

`src/bernstein/core/orchestrator.py` is 3485 lines — the entire orchestration loop, task processing, crash recovery, evolution, session management, and agent lifecycle in one file. Two agents can't work on different orchestrator features simultaneously.

## Design

Split into focused modules:

- `orchestrator.py` — Orchestrator class with tick() loop only (~500 lines)
- `tick_pipeline.py` — _tick internals: fetch tasks, group, spawn, verify
- `task_lifecycle.py` — claim, complete, fail, retry logic
- `agent_lifecycle.py` — spawn tracking, heartbeat, crash detection, kill
- `session_save.py` — session persistence, resume, orphan recovery
- `evolve_loop.py` — evolution cycle integration (already partially in evolution/)

Keep Orchestrator as the facade that delegates to these modules.

## Completion signal

- `orchestrator.py` < 600 lines
- No module > 800 lines
- All orchestrator tests pass
- tick() still works as a single entry point
