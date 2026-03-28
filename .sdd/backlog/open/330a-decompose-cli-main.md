# 330a — Decompose cli/main.py (5314 lines → modules)

**Role:** backend
**Priority:** 0 (urgent)
**Scope:** large
**Depends on:** none

## Problem

`src/bernstein/cli/main.py` is 5314 lines — a monolith containing ALL CLI commands, helpers, stop logic, sync, demo, doctor, agents, evolve, benchmark, eval, and more. This makes it impossible for agents to work on CLI features without touching the same massive file, causing merge conflicts and cognitive overload.

## Design

Split into focused modules under `src/bernstein/cli/`:

- `main.py` — click group definition + imports only (~50 lines)
- `run.py` — the main `bernstein` / `bernstein run` command
- `stop.py` — stop/force-stop/signal logic
- `status.py` — ps, status, doctor commands
- `agents.py` — agents list/sync/validate commands
- `evolve.py` — evolve review/approve/reject commands
- `eval_cmd.py` — eval/benchmark commands
- `demo.py` — demo command
- `helpers.py` — shared utilities (_server_get, _server_post, _kill_pid, etc.)

Each module registers its commands via `cli.add_command()` in main.py.

## Completion signal

- `main.py` < 100 lines
- No module > 500 lines
- All CLI commands still work
- All existing tests pass
