# Sonar findings sweeper

Imports open static-analysis findings into `.sdd/backlog/open/` as
Markdown ticket files. Re-runs are idempotent: each ticket carries the
upstream issue key in its frontmatter (`sonar_issue_key`) so the same
finding will never produce a second ticket.

## TL;DR

| What | Where |
|------|-------|
| Script | `scripts/sweep_sonar_findings.py` |
| Workflow | `.github/workflows/sweep-sonar-findings.yml` (cron dormant by default) |
| CLI entry point | `bernstein doctor sonar-sweep` |
| Output | `.sdd/backlog/open/<YYYY-MM-DD>-<type>-<slug>.md` |
| Phase 1 defaults | `BLOCKER` only, 10/day cap, GH-issue creation off |

## Env vars

| Env var | Purpose | Required? |
|---|---|---|
| `SONAR_HOST_URL` | Sonar server base URL. | yes (unless `--fixture` used) |
| `SONAR_TOKEN` | User token with `Browse` permission on the project. | yes (unless `--fixture` used) |
| `SONAR_PROJECT_KEY` | Project key. | optional (defaults to `bernstein`) |
| `GH_TOKEN` | GitHub token. | only with `--create-gh-issues` |

These match the existing `bernstein doctor sonar` env contract.

## Local invocation

```
# Dry-run against the live Sonar server (no files written).
SONAR_HOST_URL=... SONAR_TOKEN=... \
  uv run bernstein doctor sonar-sweep --dry-run

# Dry-run against a saved fixture (no network at all).
uv run bernstein doctor sonar-sweep \
  --dry-run \
  --fixture tests/unit/sweep/fixtures/issues_search.json

# Real emission, phase-1 defaults.
SONAR_HOST_URL=... SONAR_TOKEN=... \
  uv run bernstein doctor sonar-sweep \
  --severity-min BLOCKER \
  --max-per-day 10 \
  --out-dir .sdd/backlog/open
```

The script can also be called directly:

```
uv run python scripts/sweep_sonar_findings.py --help
```

## CI invocation

The workflow at `.github/workflows/sweep-sonar-findings.yml` ships with:

- `schedule: cron: '17 6 * * *'` (06:17 UTC daily).
- `workflow_dispatch` with `severity_min`, `max_per_day`, `dry_run`
  inputs so an operator can force a one-off run.

The cron trigger is gated behind an `ENABLE_CRON` workflow-level env
var that defaults to `'0'`. Scheduled runs no-op while the gate is off.
Flip `ENABLE_CRON` to `'1'` in a follow-up PR after a clean
`workflow_dispatch` smoke run.

When new tickets appear, the job opens a PR with branch
`sonar-sweep/<run-id>` and a title `chore(backlog): import sonar
findings`. The PR is review-only; no auto-merge.

## How de-dup works

1. The sweeper walks `.sdd/backlog/{open,claimed,closed,done,deferred}`.
2. It parses every `*.md`'s YAML frontmatter and collects:
   - The set of `sonar_issue_key` values across all states.
   - The set of `(sonar_rule, sonar_component, sonar_line)` triples for
     tickets in an open-ish state (`open`, `claimed`, `in_progress`,
     `blocked`).
3. For every incoming finding:
   - Skip if its `key` is in the first set.
   - Skip if its `(rule, component, line)` triple is in the second set
     (handles Sonar key churn after a refactor).

The exclusive-create open mode (`O_EXCL`) inside the emitter handles
the rare case where two concurrent runs race past the de-dup step.

## How `## Why` bodies stay safe

The script never copies the raw Sonar `message` or `htmlDesc` into the
ticket. The `## Why` body is synthesised from a pre-vetted rule-family
table in `scripts/sweep_sonar_findings.py`:

```python
RULE_FAMILY_BLURBS: tuple[tuple[str, str, str], ...] = (
    ("python:S3776", "cognitive-complexity", "Cognitive complexity exceeds..."),
    ...
)
DEFAULT_BLURB = "Static-analysis finding flagged under rule key {rule_key}..."
```

A unit test (`test_safe_why_no_forbidden_substrings`) asserts that no
blurb contains any string from `FORBIDDEN_SUBSTRINGS`, which includes
em-dash, "marketing", "funnel", "premortem", and the rest of the
project's public-artefact discipline list.

### Extending the blurb table

1. Find the rule key in the Sonar UI (e.g. `python:S5754`).
2. Pick a short category slug (`broad-except`, `hardcoded-credential`,
   etc).
3. Write a one or two sentence engineering-hygiene blurb. No marketing
   language, no vendor terms beyond "static-analysis".
4. Add the tuple to `RULE_FAMILY_BLURBS`.
5. Run `uv run pytest tests/unit/sweep/ -q` to confirm the
   forbidden-substring guard still passes.

## Phase rollout

| Phase | Severity floor | Cap/day | GH issues | Cron |
|---|---|---|---|---|
| 1 (week 1) | `BLOCKER` | 10 | off | off |
| 2 (week 2-3) | `BLOCKER`+`CRITICAL` | 20 | off | on |
| 3 (week 4+) | adds `MAJOR` | 30 | P0 only | on |

Phase transitions are operator-driven: update the workflow's input
defaults and flip `ENABLE_CRON` in a follow-up PR.

## Retracting a ticket

If a sweep-emitted ticket should not have been raised:

1. Delete the file from `.sdd/backlog/open/`.
2. Add the finding's `sonar_issue_key` to `.sdd/backlog/closed/` as a
   one-line stub with `status: closed_miss`. The dedup walker reads the
   `closed/` folder so the finding will not be re-imported.

## Tests

```
uv run pytest tests/unit/sweep/ -q
```

The suite covers de-dup (by key and by rule/component/line), idempotent
double-run, severity filter, per-day cap, the rule-family blurb
forbidden-substring guard, exclusive-create file emission, and the HTTP
retry on 5xx/429.
