# Merge queue runbook

Operator-facing notes on the GitHub merge queue for `main`. The queue is
configured by a repository **ruleset** (not legacy branch protection).

## TL;DR

| Topic | Status | Where |
|-------|--------|-------|
| What it solves | Tests the A+B *combination* before merge | this doc |
| Required check on the queue | Single `CI gate` | `.github/workflows/ci.yml` |
| Merge method | Squash | ruleset `merge_queue` rule |
| Grouping | `ALLGREEN` (all entries in a group must pass) | ruleset |
| Enable | One `gh api POST .../rulesets` call | below |
| Pause | Set ruleset `enforcement` to `disabled` | below |
| Rollback | Delete the ruleset | below |

## Why a merge queue

Branch protection on `main` has `required_status_checks.strict = false`,
so a PR is **not** required to be up to date with `main` before merging.
Two PRs can both branch from `main@X`, both pass `CI gate` against base
`X`, and both auto-merge - but the **combination** of the two is never
built or tested. That is how a red `main` lands despite two green PRs
(observed: a test fixture and a workflow change merged in separate green
PRs were red once combined).

The merge queue closes that gap. Each candidate is tested **on top of the
other queued candidates** on a synthetic `merge_group` ref before it is
allowed to merge. With `ALLGREEN` grouping, a batch only merges if the
whole batch is green; a red entry is ejected and the rest re-form.

## How the queue gates

```
PR ready ->  enters queue  ->  CI runs on merge_group ref  ->  CI gate green?
              (passes PR-                (the A+B+... combo)        |
               level required                                      yes -> merge (squash)
               checks first)                                       no  -> eject, re-form group
```

Two distinct gates, do not conflate them:

| Gate | Checks enforced | Trigger event |
|------|-----------------|---------------|
| **Enter the queue** | Legacy branch-protection required checks (`CI gate` + `review-bot-ack`) | `pull_request` |
| **Merge from the queue** | Ruleset `required_status_checks` (`CI gate` only) | `merge_group` |

`CI gate` (the `ci-gate` aggregator job in `ci.yml`) runs on `merge_group`
because the workflow declares `merge_group: {}` and `ci-gate` has
`if: always() && !cancelled()` with no event exclusion.

> **Do NOT add `review-bot-ack` to the ruleset `required_status_checks`.**
> That workflow triggers only on `pull_request` / `pull_request_review`,
> so it never reports on a `merge_group` ref. Requiring it on the queue
> would wedge every merge (the queue waits forever for a check that never
> runs). It stays a PR-entry requirement only.

## macOS coverage under the queue

The macOS matrix (`test-macos`, `adapter-integration-macos`) is **gated**:
it runs on `push` to `main`, on macOS-sensitive diffs, and on the
`macos-needed` label - not on a plain `merge_group` event. The `CI gate`
roll-up tolerates that skip on `merge_group` (see `MACOS_SKIP_EVENTS` in
`ci.yml`). Coverage is preserved because the **post-merge `push` to
`main`** runs the full macOS suite un-gated, and `ci-macos-nightly.yml` is
the daily safety net. The queue validates the integrated combination; the
merged commit validates macOS.

## Tunables (current configuration)

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `merge_method` | `SQUASH` | One commit per PR on `main` |
| `grouping_strategy` | `ALLGREEN` | A group merges only if every entry is green |
| `max_entries_to_build` | 5 | Up to 5 candidates built in parallel |
| `max_entries_to_merge` | 5 | Up to 5 merged in one batch |
| `min_entries_to_merge` | 1 | Merge as soon as 1 entry is ready (subject to wait) |
| `min_entries_to_merge_wait_minutes` | 5 | Wait up to 5 min to fill a batch before merging |
| `check_response_timeout_minutes` | 60 | A required check must report within 60 min or the entry is failed |

## Enable

The exact enable command and rollback live with whoever administers the
repo (admin scope required). Enabling is a shared-state change applied
once. The configuration is a repository ruleset targeting `main` with a
`merge_queue` rule plus a `required_status_checks` rule requiring
`CI gate`. See the PR that introduced this runbook for the precise
`gh api` invocation, or reconstruct it from the **Tunables** table above.

## Pause (keep the ruleset, stop enforcing)

Set the ruleset `enforcement` to `disabled`. PRs then merge via the
legacy branch-protection path again (no queue). Re-enable by setting
`enforcement` back to `active`.

```bash
# Find the ruleset id
gh api repos/sipyourdrink-ltd/bernstein/rulesets --jq '.[] | "\(.id)\t\(.name)"'

# Pause
gh api -X PUT repos/sipyourdrink-ltd/bernstein/rulesets/<RULESET_ID> \
  -f enforcement=disabled

# Resume
gh api -X PUT repos/sipyourdrink-ltd/bernstein/rulesets/<RULESET_ID> \
  -f enforcement=active
```

## Rollback (remove the queue entirely)

```bash
gh api -X DELETE repos/sipyourdrink-ltd/bernstein/rulesets/<RULESET_ID>
```

After deletion, `main` reverts to the legacy branch-protection required
checks. Any PRs sitting in the queue are released back to normal merge.

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Nothing merges; entries sit in queue | A required check does not run on `merge_group` | Confirm the ruleset requires only `CI gate`; remove any PR-only check |
| `CI gate` red on `merge_group` but green on the PR | A real combination failure, or a job skipped that the roll-up does not tolerate | Read the `merge_group` run log; if a legitimately-skipped job is flagged, extend the roll-up tolerance in `ci.yml` |
| Entry failed after 60 min | A required check never reported | Check runner queue saturation; the entry is ejected and re-formed automatically |
| Queue throughput too low | `min_entries_to_merge_wait_minutes` batching | Lower the wait or raise `max_entries_to_merge` |

The `merge_group` path is guarded by regression tests in
`tests/unit/test_required_check_canary_workflow_yaml.py` that execute the
shipped `ci-gate` roll-up against a synthetic `merge_group` payload. If a
future change to `ci.yml` would wedge the queue, those tests fail in CI
before the change can merge.
