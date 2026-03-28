---
id: standard-004
title: Add structured logging to task server
role: backend
expected_files_modified:
  - src/bernstein/core/server.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/server.py :: logger.info"
  - type: test_passes
    value: "uv run pytest tests/unit/test_server.py -x -q"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/server.py
---

Add structured logging to the task creation and completion endpoints in
`src/bernstein/core/server.py`. Each log line should include the task ID
and the action performed. Use the standard `logging` module.
