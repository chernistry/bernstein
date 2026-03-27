# Fix test regressions from previous agent runs

**Role:** qa
**Priority:** 1 (critical)
**Scope:** medium
**Complexity:** medium

## Problem
Previous agent runs modified code but left some tests in an inconsistent state. The test suite passes (1050) but there may be tests that test the wrong thing or mock things that changed.

## Tasks
1. Run full test suite: `uv run pytest tests/ -x -q --tb=short`
2. Check for tests that mock non-existent attributes (e.g. test_janitor mocking `call_llm`)
3. Check for tests that assert stale values
4. Fix any broken tests
5. Ensure coverage doesn't decrease

## Files
- tests/unit/*.py — fix broken tests
- Any source files that tests reference

## Acceptance criteria
- All tests pass with no warnings about mocking non-existent attributes
- No tests skip or xfail without documented reason


---
**completed**: 2026-03-28 01:09:57
**task_id**: 14b8f082903e
**result**: Fixed 6 test regressions: 4 in test_researcher.py (wrong mock target for tavily_search), 2 in test_router.py (stale effort assertion high→max for large+high tasks). All 1063 tests pass clean.
