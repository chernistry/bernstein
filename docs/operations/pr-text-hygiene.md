# PR text hygiene runbook

Operator-facing notes on the preventive lint that runs on every pull
request and refuses to pass if the PR text contains a phrase on the
deny-list.

## TL;DR

| Topic | Where |
|-------|-------|
| Workflow | `.github/workflows/pr-text-hygiene.yml` |
| Script | `scripts/check_pr_text_hygiene.py` |
| Deny-list | `.github/pr-text-hygiene-deny.json` |
| Tests | `tests/unit/scripts/test_pr_text_hygiene.py` |
| Required check name | `pr-text-hygiene / text-hygiene` |
| Per-PR opt-out label | `skip-text-hygiene` |

## What the gate does

On every `pull_request` open / edit / synchronize / reopen, the
`pr-text-hygiene` workflow checks out the PR head, collects every
commit subject + body between the base and the head SHA, and runs
`scripts/check_pr_text_hygiene.py` against four surfaces:

1. The PR title
2. The PR body (markdown)
3. The PR head branch name
4. Each commit subject + body in the PR

Matching is plain case-insensitive substring matching against the
phrases in `.github/pr-text-hygiene-deny.json`. Each match prints a
GitHub Actions annotation of the form
`::error file=<surface>::<phrase> matched in <surface>` and the job
exits with status 1.

Bot authors (`dependabot[bot]`, `renovate[bot]`,
`github-actions[bot]`, `bernstein[bot]`,
`bernstein-orchestrator[bot]`) are skipped so the gate cannot loop
against itself.

## Adding a phrase to the deny-list

1. Edit `.github/pr-text-hygiene-deny.json`. The schema is one object
   with a single `denylist` key whose value is a JSON list of
   non-empty strings.
2. Run the unit tests:
   ```
   uv run pytest tests/unit/scripts/test_pr_text_hygiene.py -q
   ```
3. Run the script locally against your PR text to make sure your own
   PR does not trip on the new entry:
   ```
   python scripts/check_pr_text_hygiene.py \
     --title "<your pr title>" \
     --body "" \
     --branch "$(git rev-parse --abbrev-ref HEAD)" \
     --commit-messages-file <(git log origin/main..HEAD --format='%B%n---')
   ```
4. Open a PR with the change.

## Overriding the gate per-PR

If a PR legitimately needs to keep a phrase that is on the deny-list
(for example, a docs PR that quotes the term to define what is being
removed), add the `skip-text-hygiene` label to that PR. The workflow
honours the label and skips the `text-hygiene` job entirely.

The label is workflow-level. The script itself never reads PR labels
(verified by a dedicated unit test).

## Pinning `text-hygiene` as a required check

Pull the current branch protection payload, add the new check name to
the `required_status_checks.contexts` list, and `PUT` the merged
payload back. Replace `OWNER` and `REPO` with the repository
coordinates.

```bash
gh api -H 'Accept: application/vnd.github+json' \
  repos/OWNER/REPO/branches/main/protection \
  > /tmp/main-protection.json

jq '
  .required_status_checks.contexts = (
    (.required_status_checks.contexts // [])
    + ["pr-text-hygiene / text-hygiene"]
    | unique
  )
' /tmp/main-protection.json > /tmp/main-protection.next.json

gh api -X PUT -H 'Accept: application/vnd.github+json' \
  --input /tmp/main-protection.next.json \
  repos/OWNER/REPO/branches/main/protection
```

After the `PUT` returns, open one fresh PR and confirm that
`pr-text-hygiene / text-hygiene` appears in the merge box as a
required check.

## Local self-check before opening a PR

```
python scripts/check_pr_text_hygiene.py \
  --title "<pr title>" \
  --body "$(cat pr-body-draft.md 2>/dev/null || echo '')" \
  --branch "$(git rev-parse --abbrev-ref HEAD)" \
  --commit-messages-file <(git log origin/main..HEAD --format='%B%n---')
```

Exit code 0 means the PR text is clean. Exit code 1 means at least
one phrase matched; the annotations printed identify the surface and
the offending phrase so you can rewrite the text before pushing.
