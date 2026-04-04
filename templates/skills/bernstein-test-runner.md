---
name: bernstein-test-runner
description: Run project tests correctly to avoid memory leaks and failures
whenToUse: When running tests, checking test results, or verifying code correctness
---

**IMPORTANT**: Always use the project's test runner script, never run pytest directly on the full suite.

Run all tests (isolated per-file, prevents memory leaks):

```bash
uv run python scripts/run_tests.py -x
```

Run a single test file:

```bash
uv run pytest tests/unit/test_foo.py -x -q
```

**NEVER** run `uv run pytest tests/ -x -q` — this leaks 100+ GB RAM across 2000+ tests.

Check linting before completing:

```bash
uv run ruff check src/
```
