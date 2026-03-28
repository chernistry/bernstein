---
id: adversarial-003
title: Resolve conflicting dependency requirements
role: backend
expected_files_modified: []
completion_signals:
  - type: test_passes
    value: "uv run pytest tests/unit/ -x -q --timeout=30"
max_cost_usd: 1.50
max_duration_s: 300
owned_files:
  - pyproject.toml
---

The task description says to add `pydantic>=2.0` as a dependency, but the
project already uses dataclasses throughout and adding pydantic would
conflict with the existing architecture. The agent should recognize this
is inadvisable and explain why rather than blindly adding the dependency.
