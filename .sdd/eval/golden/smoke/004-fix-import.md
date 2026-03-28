---
id: smoke-004
title: Fix unused import
role: backend
expected_files_modified:
  - src/bernstein/core/context.py
completion_signals:
  - type: command_succeeds
    value: "uv run ruff check src/bernstein/core/context.py"
max_cost_usd: 0.10
max_duration_s: 60
owned_files:
  - src/bernstein/core/context.py
---

Remove any unused imports from `src/bernstein/core/context.py` so that
`ruff check` passes with no warnings for that file.
