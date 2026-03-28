---
id: standard-005
title: Fix edge case in cost tracker
role: backend
expected_files_modified:
  - src/bernstein/core/cost_tracker.py
  - tests/unit/test_cost_tracker.py
completion_signals:
  - type: test_passes
    value: "uv run pytest tests/unit/test_cost_tracker.py -x -q"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/cost_tracker.py
  - tests/unit/test_cost_tracker.py
---

Handle the edge case where `record_cost` is called with a negative amount.
The method should raise a `ValueError` instead of silently recording it.
Add a test verifying the error is raised with a descriptive message.
