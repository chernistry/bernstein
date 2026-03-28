---
id: stretch-003
title: Add adapter compatibility test matrix
role: qa
expected_files_modified:
  - tests/unit/test_adapters.py
completion_signals:
  - type: test_passes
    value: "uv run pytest tests/unit/test_adapters.py -x -q"
  - type: file_contains
    value: "tests/unit/test_adapters.py :: parametrize"
max_cost_usd: 2.00
max_duration_s: 600
owned_files:
  - tests/unit/test_adapters.py
---

Create a parametrized test matrix in `tests/unit/test_adapters.py` that
verifies all adapter classes (Claude, Codex, Gemini, Qwen, Aider) implement
the base adapter interface correctly: `spawn`, `send_task`, and `collect_result`
methods exist and have compatible signatures. Use `pytest.mark.parametrize`
to avoid duplicating test logic per adapter.
