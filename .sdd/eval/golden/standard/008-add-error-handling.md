---
id: standard-008
title: Add graceful error handling for missing task
role: backend
expected_files_modified:
  - src/bernstein/core/server.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/server.py :: 404"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/server.py
---

Ensure the `POST /tasks/{id}/complete` endpoint returns a 404 JSON response
with `{"error": "task not found"}` when the task ID does not exist, instead
of raising an unhandled exception. Add the same handling to `/tasks/{id}/fail`.
