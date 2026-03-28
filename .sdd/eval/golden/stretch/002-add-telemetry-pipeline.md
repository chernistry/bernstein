---
id: stretch-002
title: Add telemetry aggregation pipeline
role: architect
expected_files_modified:
  - src/bernstein/eval/telemetry.py
  - src/bernstein/eval/harness.py
  - tests/unit/test_eval_harness.py
completion_signals:
  - type: file_contains
    value: "src/bernstein/eval/telemetry.py :: aggregate"
  - type: test_passes
    value: "uv run pytest tests/unit/test_eval_harness.py -x -q"
max_cost_usd: 2.00
max_duration_s: 600
owned_files:
  - src/bernstein/eval/telemetry.py
  - src/bernstein/eval/harness.py
  - tests/unit/test_eval_harness.py
---

Add an `aggregate_telemetry` function to `src/bernstein/eval/telemetry.py`
that takes a list of `AgentTelemetry` objects and returns summary statistics:
total duration, total cost, mean signals passed rate, and per-role breakdowns.
Wire it into the harness's `compute_multiplicative_score` method and add tests.
