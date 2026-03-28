# 613 — GitHub Action

**Role:** devops
**Priority:** 2 (high)
**Scope:** medium
**Depends on:** #602

## Problem

There is no way to trigger Bernstein orchestration from CI. GitHub Agentic Workflows validates the category of CI-triggered agent runs. Without a GitHub Action, users must manually invoke Bernstein after CI failures or for automated maintenance tasks.

## Design

Build a `bernstein-action` GitHub Action that triggers orchestration from CI workflows. Primary use case: on CI failure, spawn Bernstein agents to diagnose and fix the issue. The action accepts inputs: task description, budget limit, model preference, and retry count. It installs Bernstein, configures it with repo context, runs the orchestration, and reports results as PR comments or check annotations. Package as a composite action using `action.yml`. Support both "fix CI failure" and "run arbitrary task" modes. Include example workflow YAML files for common scenarios. The action should work with the repository's existing `.sdd/` configuration if present.

## Files to modify

- `action.yml` (new)
- `action/entrypoint.sh` (new)
- `.github/workflows/bernstein-ci-fix.yml` (new — example)
- `docs/github-action.md` (new)

## Completion signal

- GitHub Action installs and runs Bernstein in a CI workflow
- On CI failure, action spawns agents and pushes a fix
- Example workflow YAML works when copied into a repo
