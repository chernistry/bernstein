# Flake handling

How Bernstein detects, quarantines, and recovers from flaky tests.

## TL;DR

- Two pieces of CI machinery: ctrf-io test reporter (per-PR markdown
  summary) and pytest-xflaky (nightly auto-quarantine PRs).
- A "flaky" test is one that fails at least twice AND passes at least
  twice across five consecutive runs of the unit suite under
  randomised ordering.
- Quarantined tests get `@pytest.mark.xfail(strict=False)` - they
  still run, still emit XPASS when they pass, but stop blocking the
  merge gate.
- Unquarantining requires three consecutive green runs after the
  decorator is removed.

## Pipeline

### 1. Per-PR test summary (ctrf)

`ctrf-io/github-test-reporter` runs as a step in `ci.yml::test` after
the JUnit producer step. It consumes the same `junit.xml` that
mikepenz/action-junit-report already publishes and:

- Writes a markdown summary to the workflow Step Summary tab.
- Posts (or updates) a sticky comment on the triggering PR with the
  same content.
- Uploads the converted CTRF JSON as the `ctrf-report` workflow
  artifact (7-day retention, sized to the xflaky look-back window).

The reporter highlights failed tests, slowest tests, and previously-
flaky tests when present.

### 2. Nightly flake detection (pytest-xflaky)

`.github/workflows/flake-quarantine.yml` runs at 04:00 UTC every day
and on `workflow_dispatch`. It:

1. Installs the `dev` group plus `pytest-randomly` (the latter only
   in this ephemeral runner - it is intentionally not a global dev
   dep because it auto-activates on import and would shuffle every
   other CI job's test order).
2. Runs `pytest tests/unit --xflaky-collect --json-report` five
   times: once with `-p no:randomly` for a deterministic baseline,
   then four times with `--randomly-seed=last` to chain seeds.
3. Generates the xflaky reports
   (`--xflaky-report --xflaky-github-report`) with the threshold
   `--xflaky-min-failures 2 --xflaky-min-successes 2`.
4. If any test was flagged, runs `pytest --xflaky-fix` to rewrite
   the offending test files with `@pytest.mark.xfail(strict=False)`.
5. Opens a PR via `peter-evans/create-pull-request` against `main`
   with the label set `ci, tests, flaky, bot`.

The PR body explains the threshold, links to the artifacts, and gives
operators an explicit checklist for investigation and unquarantining.

## Operator workflow

### Reviewing a quarantine PR

1. Open the PR from branch `bot/flake-quarantine`.
2. Read the affected test names. If they cluster around a single
   subsystem (network, async event loops, filesystem races), that is
   strong evidence of a shared root cause - file a bug to track the
   underlying defect.
3. Pull the `xflaky-reports` artifact for per-run details.
4. Land the PR if the markers look reasonable. Close it if the run
   was infra noise - the next nightly run will redetect any real
   flake.

### Investigating a quarantined test

1. Pull the branch locally.
2. Reproduce in isolation:
   ```
   uv run pytest path/to/test_file.py::TestClass::test_method -x -v
   ```
3. Reproduce under randomised order:
   ```
   uv run pytest path/to/test_file.py --randomly-seed=<seed-from-report>
   ```
4. Reproduce under parallel execution (if parallel-safe):
   ```
   uv run pytest path/to/test_file.py -n auto
   ```
5. Common root causes for our codebase:
   - Shared mutable global state across the agent registry.
   - Hidden network calls (use `respx` to make them deterministic).
   - Time-of-day assumptions (use `freezegun`).
   - Filesystem races on `tmp_path` cleanup.
   - Event-loop bleed between async tests (check pytest-asyncio mode).
6. Fix the root cause, remove the decorator, push.

### Unquarantining policy

A test may be removed from quarantine when:

- The root cause is identified and fixed (preferred).
- The test passes three consecutive runs after the decorator is
  removed (operator runs `gh workflow run ci.yml --ref <branch>`
  two extra times after the initial run).

If neither condition holds, the quarantine stays and the underlying
defect is tracked as a regular bug.

## Threshold tuning

The current thresholds (5 runs, min 2 failures, min 2 successes)
trade off detection latency for false-positive rate:

- Lower `--xflaky-min-failures` would catch slower-flaking tests
  faster but file false-positive PRs more often.
- More runs per night would tighten the signal at the cost of hosted-
  runner minutes - the current five-run budget is ~25 minutes per
  unit suite on a `ubuntu-latest` hosted runner.

Both knobs live in `.github/workflows/flake-quarantine.yml`. Tune in
that file rather than per-PR.

## Out of scope

- Auto-unquarantine after a green-run streak. Easy to get wrong
  (XPASS noise vs. real recovery); tracked as a separate ticket.
- BuildPulse / flaky.io paid SaaS. Free-tier-only constraint.
- Test isolation primitives (`pytest-randomly` as a global dev dep).
  pytest-randomly stays local to the nightly job for the reason
  above.
