# 602 — CI Auto-Fix Pipeline

**Role:** backend
**Priority:** 1 (critical)
**Scope:** medium
**Depends on:** none

## Problem

When CI fails, developers manually read logs, diagnose the issue, and push a fix. This cycle wastes 15-30 minutes per failure. ComposioHQ Agent Orchestra validated that AI agents can reliably fix CI failures. Bernstein has no CI failure detection or auto-fix capability.

## Design

Build a CI failure detection and auto-fix pipeline. The system watches for CI failure events (via webhook or polling), downloads the CI log, parses it to extract the failure reason, and spawns an agent to diagnose and fix the issue. The agent reads the relevant source files, creates a fix, commits to a branch, and pushes to re-trigger CI. Implement configurable retry limits (default: 3 attempts) to prevent infinite fix loops. Support GitHub Actions log format initially, with an adapter pattern for other CI systems. The fix agent should use a specialized system prompt that focuses on test failures, lint errors, and build errors.

## Files to modify

- `src/bernstein/core/ci_fix.py` (new/enhance)
- `src/bernstein/core/ci_log_parser.py` (new)
- `src/bernstein/adapters/ci/github_actions.py` (new)
- `templates/roles/ci-fixer.md` (new)
- `tests/unit/test_ci_fix.py` (new/enhance)

## Completion signal

- Given a failed CI run URL, Bernstein spawns an agent that pushes a fix
- Retry limit is enforced (stops after N attempts)
- CI log parsing extracts failure reason for GitHub Actions format
