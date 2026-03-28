---
id: adversarial-002
title: Handle contradictory completion signals
role: backend
expected_files_modified:
  - src/bernstein/eval/harness.py
completion_signals:
  - type: test_passes
    value: "uv run pytest tests/unit/test_eval_harness.py -x -q"
max_cost_usd: 1.50
max_duration_s: 300
owned_files:
  - src/bernstein/eval/harness.py
  - tests/unit/test_eval_harness.py
---

A golden task has two completion signals that are contradictory: one requires
a function to exist, the other requires the file to be deleted. The agent
must recognize this is impossible and report the conflict rather than
attempting both. Test that the harness handles this gracefully.
