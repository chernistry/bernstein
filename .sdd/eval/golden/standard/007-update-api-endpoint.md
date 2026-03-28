---
id: standard-007
title: Add task filtering by role
role: backend
expected_files_modified:
  - src/bernstein/core/server.py
  - tests/unit/test_server.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/server.py :: role"
  - type: test_passes
    value: "uv run pytest tests/unit/test_server.py -x -q"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/server.py
  - tests/unit/test_server.py
---

Add an optional `role` query parameter to `GET /tasks` that filters results
to only tasks assigned to the given role. When not provided, return all tasks
as before. Add a test verifying both filtered and unfiltered responses.
