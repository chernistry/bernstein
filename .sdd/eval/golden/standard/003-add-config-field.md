---
id: standard-003
title: Add config field to OrchestratorConfig
role: backend
expected_files_modified:
  - src/bernstein/core/models.py
  - tests/unit/test_orchestrator.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/core/models.py :: max_retries"
  - type: test_passes
    value: "uv run pytest tests/unit/test_orchestrator.py -x -q"
max_cost_usd: 0.50
max_duration_s: 180
owned_files:
  - src/bernstein/core/models.py
  - tests/unit/test_orchestrator.py
---

Add a `max_retries: int = 3` field to the `OrchestratorConfig` dataclass in
`src/bernstein/core/models.py`. Add a test in `tests/unit/test_orchestrator.py`
verifying the default value and that a custom value can be set.
