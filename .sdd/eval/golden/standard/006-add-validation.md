---
id: standard-006
title: Add task ID validation
role: backend
expected_files_modified:
  - src/bernstein/core/models.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/models.py :: validate"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/models.py
---

Add a `__post_init__` validation to the `Task` dataclass that ensures the
`id` field matches the pattern `[a-z0-9-]+` (lowercase alphanumeric and
hyphens only). Raise `ValueError` if the ID is invalid.
