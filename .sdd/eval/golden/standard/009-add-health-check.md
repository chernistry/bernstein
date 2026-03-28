---
id: standard-009
title: Add health check endpoint
role: backend
expected_files_modified:
  - src/bernstein/core/server.py
  - tests/unit/test_server.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/server.py :: /health"
  - type: test_passes
    value: "uv run pytest tests/unit/test_server.py -x -q"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/server.py
  - tests/unit/test_server.py
---

Add a `GET /health` endpoint that returns `{"status": "ok", "version": "..."}`.
The version should be read from `bernstein.__version__` if available, or
`"unknown"` as fallback. Add a test verifying the response shape.
