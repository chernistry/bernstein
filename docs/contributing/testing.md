# Testing & CI Hardening Reference

This page documents the test tooling we run on every PR and overnight,
what bug class each tool catches, and how to reproduce a CI failure
locally without waiting for the cloud runner.

## Tool ↔ bug-class matrix

| Tool                        | Bug class                                                         | When                |
| --------------------------- | ----------------------------------------------------------------- | ------------------- |
| **Hypothesis** (property)   | Hash-chain breaks, signature roundtrip, canonical-bytes drift     | PR (smoke), nightly (deep)  |
| **Schemathesis**            | 5xx leaks against fuzzed REST inputs                              | PR (allow-list), nightly (full)  |
| **CrossHair**               | Logic errors in pure helpers (concolic execution, assert checks)  | nightly only (until contracts added)         |
| **mutmut diff-only**        | Test-effectiveness gaps on PR-changed lines                       | PR (advisory)       |
| **mutmut full**             | Test-effectiveness gaps across the whole repo                     | nightly (advisory)  |
| **Semgrep** (custom rules)  | eval/exec/pickle in production, env-leak in `_spawn_*`            | PR (ERROR fails)    |
| **Bandit**                  | Generic Python security smells (shell=True, weak hash, tarfile)   | PR (HIGH only)      |
| **pip-audit**               | Known PyPI CVEs in production deps                                | PR (strict)         |
| **Beartype** (claw)         | Runtime type-contract violations on public security/cluster APIs  | PR                  |
| **syrupy** (snapshot)       | JSONL/audit/lineage wire-format drift                             | PR                  |
| **Pyright strict zone**     | Untyped/implicit-Any leakage in `core/security/`, `core/protocols/cluster/` | PR                  |
| **Vulture**                 | Dead code (unused functions/classes/vars at confidence ≥80)       | PR                  |
| **diff-cover**              | <80% coverage on lines this PR changed                            | PR (advisory)       |
| **import-linter**           | Architecture-contract violations (cross-package imports)          | PR                  |
| **ruff** + **typos**        | Lint, format drift, common typos                                  | PR                  |

## Run any of the above locally

```bash
# Property suite (smoke)
HYPOTHESIS_PROFILE=smoke uv run pytest tests/property/ -q --no-cov

# Property suite (deep — same as nightly)
HYPOTHESIS_PROFILE=deep uv run pytest tests/property/ -q --no-cov

# Snapshot tests
uv run pytest tests/snapshot/ -q --no-cov
# Update snapshots after an intentional schema change:
uv run pytest tests/snapshot/ -q --no-cov --snapshot-update

# Schemathesis (smoke — only the critical-surface allow-list)
BERNSTEIN_AUTH_DISABLED=1 SCHEMATHESIS_PROFILE=smoke \
  uv run pytest tests/contract/ -q --no-cov

# Semgrep (project rules; ERROR severity is the PR gate).
# Install once via `uv tool install semgrep` — semgrep's transitive
# pins (click<8.2, opentelemetry-sdk<1.26) conflict with our project
# floors, so it lives in its own venv outside `uv sync`.
uv tool install semgrep
uv tool run semgrep --config .semgrep.yml --severity ERROR --error src/

# Bandit (production HIGH-only with baseline)
uv run bandit -r src/ -ll --severity-level high -b .bandit-baseline.json

# pip-audit
uv run pip-audit --strict

# Beartype claw — runs the focused unit tests under runtime type
# enforcement on core.security + core.agents + core.protocols.cluster
BEARTYPE_USE_CLAW=enable \
  uv run pytest tests/unit/ -q --no-cov \
  -k 'security or agent or cluster or audit or lineage'

# Pyright strict zone
uv run pyright --typecheckingmode strict \
  src/bernstein/core/security/ \
  src/bernstein/core/protocols/cluster/

# Vulture
vulture src/ vulture_whitelist.py --min-confidence 80 --exclude tests,docs

# Diff-cover (after a coverage run)
uv run pytest tests/unit/ --cov=src/bernstein --cov-report=xml
uv run diff-cover coverage.xml --compare-branch=origin/main --fail-under=80
```

## When a tool fires on you

### Semgrep ERROR
The rule is intentionally tight. If you genuinely need the pattern,
add the inline `# nosemgrep: <rule-id>  -- <one-line justification>`
comment. PRs that disable a rule without justification get bounced.

### Bandit HIGH
Only HIGH fails the PR; the 11 pre-existing HIGH findings on `main`
are captured in `.bandit-baseline.json`. New HIGH findings need either
a fix or an explicit baseline update with rationale in the PR
description.

### Hypothesis falsifying example
The error output includes a `git apply .hypothesis/patches/...` line.
Apply that patch to add the failing example as a deterministic
regression case, fix the bug, and the patch becomes a permanent unit
test.

### Schemathesis 5xx leak
A real bug — an endpoint should never propagate an unhandled
exception. The reproducer is printed at the bottom of the failure (a
`curl` invocation against the mounted ASGI app).

### Snapshot diff
If the diff is intentional (you changed an audit field on purpose),
re-run with `--snapshot-update` and commit the updated `.ambr`. If
not, you've caused unintended wire-format drift.

### mutmut survivor
The mutation operator changed `==` to `!=` (etc.) and no test
caught it. Either add a test that distinguishes the two operators
or, if the mutation is genuinely undetectable (e.g. an off-by-one
in a comment-only path), document why in `mutmut_config.py`.

## CI cost budget

PR-time CI must stay under 2× the pre-2026 baseline. Heavy work
(full mutmut, deep Hypothesis, full Schemathesis, full CrossHair)
runs only in `nightly-deep-tests.yml` (cron `0 3 * * *`) and is
explicitly `continue-on-error` so an overnight regression doesn't
block tomorrow's PRs.

The added PR-time jobs target ≤8 min wall-clock each and run in
parallel after the lint job clears (so a typo PR fails fast in <2
min without burning compute on the heavy stack).
