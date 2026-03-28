---
id: stretch-004
title: Add concurrency safety test for task store
role: qa
expected_files_modified:
  - tests/unit/test_task_store.py
completion_signals:
  - type: test_passes
    value: "uv run pytest tests/unit/test_task_store.py -x -q"
max_cost_usd: 2.00
max_duration_s: 600
owned_files:
  - tests/unit/test_task_store.py
---

Write a test that spawns 10 concurrent asyncio tasks, each calling
`complete_task` on the same task store simultaneously. Verify that exactly
one succeeds and the rest get a conflict error. This tests the file-lock
safety of the task store under contention.
