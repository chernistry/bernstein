---
id: smoke-005
title: Rename variable to snake_case
role: backend
expected_files_modified:
  - src/bernstein/core/cost_tracker.py
completion_signals:
  - type: command_succeeds
    value: "uv run ruff check src/bernstein/core/cost_tracker.py"
max_cost_usd: 0.10
max_duration_s: 60
owned_files:
  - src/bernstein/core/cost_tracker.py
---

Find any variable in `src/bernstein/core/cost_tracker.py` that uses camelCase
and rename it to snake_case. Ensure all references are updated consistently.
