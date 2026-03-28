---
id: standard-010
title: Add TaskID type alias
role: backend
expected_files_modified:
  - src/bernstein/core/models.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/models.py :: TaskID"
max_cost_usd: 0.30
max_duration_s: 120
owned_files:
  - src/bernstein/core/models.py
---

Define a `TaskID = str` type alias in `src/bernstein/core/models.py` and use
it in the `Task` dataclass's `id` field and in the `parent_id` field. This
improves readability without changing runtime behavior.
