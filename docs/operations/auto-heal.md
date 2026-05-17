# Auto-heal CI

Self-healing CI workflow for safe + heuristic autofix classes.

## TL;DR

| Class       | Examples                                              | Action |
|-------------|-------------------------------------------------------|--------|
| safe        | `Lint`, `Repo hygiene`, `Dead code (Vulture)`         | apply mechanical fix in-place |
| heuristic   | `Spelling (typos)`                                    | parse log, auto-allowlist vendor-shaped tokens |
| risky       | `Test (...)`, `Type check`, `CodeQL`, `Bandit`, etc.  | emit warning, do nothing |
| unknown     | any unrecognised job                                  | treated as risky |

The workflow opens a `fix(ci-heal): ...` PR on the `ci-heal/<short-sha>`
branch. The PR runs CI like any human PR. Merge happens only after CI on
the PR is green.

## When it fires

Trigger:

```yaml
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]
```

Plus the `if:` guard requires:

- `conclusion == 'failure'`
- `head_branch == 'main'`
- canonical-repo (no fork PRs)
- commit message does NOT start with `fix(ci-heal):` (anti-recursion)
- actor is not `github-actions[bot]`

## What it does

1. `gh run view --json jobs` returns every failed job name.
2. `scripts/auto_heal_categorize.py` buckets them into
   `safe / heuristic / risky / unknown`.
3. For `safe`:
   - `Lint` -> `uv run ruff format src/ tests/ scripts/` +
     `uv run ruff check --fix src/ tests/ scripts/`.
   - `Repo hygiene` -> `uv run bernstein agents-md sync`.
4. For `heuristic`:
   - `Spelling (typos)` -> download the failing job log,
     `scripts/auto_heal_typos.py` extracts vendor-field-shaped tokens
     (snake_case-lower, length 8+ or containing a digit / underscore,
     not on the prose-typo denylist), then
     `scripts/auto_heal_apply_typos.py` appends them to
     `typos.toml`'s `[default.extend-words]` section.
5. Self-test the fix locally (run `typos`, `ruff check`,
   `bernstein agents-md verify`) before pushing.
6. Idempotency check: bail with success when `git diff --quiet`.
7. Cordon check: enforce that the diff only touches the allowlist:
   - `typos.toml`, `.typos.toml`
   - `AGENTS.md`, `CLAUDE.md`, `.goosehints`, `CONVENTIONS.md`
   - `.cursor/rules/*.mdc`
   - Anything else MUST pass `git diff -w --quiet` (whitespace-only
     ruff-format change).
8. Commit and push to `ci-heal/<short-sha>`.
9. Open a PR with label `auto-heal` and the failing-jobs summary table.

## What it will NOT do

| Will not touch              | Reason |
|-----------------------------|--------|
| Real test failures          | A failing test is a signal, not a typo. |
| Type-check / Pyright errors | These imply real type drift -- needs human review. |
| CodeQL / Bandit / Semgrep   | Security findings are never auto-allowlisted. |
| pip-audit / Schemathesis    | Dependency or contract regressions. |
| Mutation / Property tests   | Logic correctness signals. |
| Beartype                    | Runtime type-contract violations. |
| Business-logic source files | Cordon allowlist rejects any non-whitespace diff outside the allowlisted paths. |

If a risky job fails, the workflow emits a GitHub Actions warning
listing the jobs but does not push anything.

## Safety rails

| Rail                 | Where                                                  |
|----------------------|--------------------------------------------------------|
| Job-level perms      | `triage` is read-only; `heal` is `contents:write + pull-requests:write` (no `actions:write`). |
| Anti-recursion       | `workflow_run.workflows: ["CI"]` (never `Auto-heal`); commit-prefix guard `fix(ci-heal):`. |
| Concurrency          | `auto-heal-<sha>` group, `cancel-in-progress: true`. |
| Idempotency          | Existing `ci-heal/<sha>` PR short-circuits. |
| Per-SHA budget       | At most 3 heal PRs per failing SHA per hour. |
| Recurrence detection | If 2 of the last 5 closed heal PRs of the same class did not merge, bail. |
| Cordon               | Diff must be in the allowlist OR whitespace-only. |
| Self-test            | Local run of the fixed-up command must pass before push. |
| Pinned actions       | All `uses:` refs pinned to 40-char SHA. |
| No `--no-verify`     | Pre-commit hooks (if any) are honoured. |
| No force-push        | Branch is created fresh from main HEAD each run. |

## Outcomes

| Outcome      | Meaning                                                              |
|--------------|----------------------------------------------------------------------|
| `pr_opened`  | Diff produced, cordon passed, self-test passed, PR opened.           |
| `no_changes` | Autofix produced no diff. Underlying failure is not auto-healable.   |
| (bailed)     | Budget exhausted, recurrence detected, or cordon violated. Workflow log explains. |

## Disabling

Temporary disable:

```bash
gh workflow disable auto-heal.yml
```

Re-enable:

```bash
gh workflow enable auto-heal.yml
```

## Audit trail

The workflow writes everything to its own GitHub Actions run log:

- categorized job buckets,
- candidate tokens emitted by the typos heuristic,
- cordon-check verdict and diff stat,
- PR URL on success.

`gh run list --workflow=auto-heal.yml` is the canonical audit feed.

## Escalation

If auto-heal cannot fix a failure or its PR also goes red:

1. Check the `auto-heal` label PR list:
   `gh pr list --label auto-heal --state open`.
2. Investigate the failing job manually -- it is by definition
   `risky` / `unknown` and not in the autofix scope.
3. If the heuristic typos path is going wrong, audit recent
   `auto-heal: vendor field token` lines in `typos.toml` and remove
   any that look like real prose typos.
4. The companion LLM-backed path
   (`.github/workflows/bernstein-ci-fix.yml`) may have opened its own
   `auto-heal/<sha>` PR; both can coexist.

## Related

- `.github/workflows/ci.yml` -- the workflow this listens to.
- `.github/workflows/bernstein-ci-fix.yml` -- LLM-backed CI repair.
- `scripts/auto_heal_categorize.py` -- safety-class classifier.
- `scripts/auto_heal_typos.py` -- vendor-field token extractor.
- `scripts/auto_heal_apply_typos.py` -- `typos.toml` writer.
