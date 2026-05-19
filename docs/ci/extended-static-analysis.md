# Extended static analysis

Additional static-analysis surface that complements the existing
ruff + mypy + bandit + CodeQL lane. Each tool catches a different
bug class; together they close gaps that single-tool runs miss.

Workflow: `.github/workflows/static-analysis-extended.yml`.

## TL;DR

| Job          | Tool      | Gate           | SARIF | Where findings show |
|--------------|-----------|----------------|-------|---------------------|
| `semgrep`    | Semgrep CE| Fail on new    | Yes   | Security tab        |
| `trivy-fs`   | Trivy     | Fail HIGH/CRIT | Yes   | Security tab        |
| `trivy-iac`  | Trivy     | Fail HIGH/CRIT | Yes   | Security tab        |
| `vulture`    | vulture   | Advisory       | Yes   | Security tab        |
| `refurb`     | refurb    | Advisory       | Yes   | Security tab        |
| `perflint`   | pylint+perflint | Advisory | Yes   | Security tab        |

All jobs run in parallel; total wall-clock is dominated by Semgrep
(under 5 minutes for the current `src/` tree).

## What each tool catches

| Tool      | Bug class                                                    |
|-----------|--------------------------------------------------------------|
| Semgrep CE| Pattern-based Python issues that CodeQL free tier skips      |
| Trivy fs  | CVEs in lockfile deps + leaked secrets in tracked files      |
| Trivy IaC | Dockerfile / docker-compose / helm / kustomize misconfigs    |
| vulture   | Unused functions / classes / vars across the 40-adapter tree |
| refurb    | Outdated Python idioms with cleaner modern equivalents       |
| perflint  | Hot-path antipatterns (string concat in loops, etc.)         |

This stack does not replace ruff / mypy / bandit / CodeQL; it adds
the surface those tools do not cover.

## Triggers

- `push` to `main` (path-filtered to source / config / IaC / workflow).
- `pull_request` (same path filter).
- Weekly cron Sunday 05:23 UTC as a safety net for dormant findings.
- `workflow_dispatch` for ad-hoc runs.

## Baseline policy

### Semgrep

`.semgrep/baseline.yml` records pre-existing findings on `main` for
transparency and audit. The actual gate is git-baseline based:
`semgrep scan --baseline-commit=<base-sha>` on pull requests so only
new findings introduced by the PR fail the job.

To regenerate the snapshot file:

```
uv tool run semgrep scan \
    --config p/python \
    --config p/security-audit \
    --severity ERROR --severity WARNING \
    --json --metrics off --quiet src/ \
    > /tmp/semgrep.json
```

Then either update `.semgrep/baseline.yml` by hand or write a small
script to convert the JSON.

### Trivy

No baseline. HIGH / CRITICAL findings fail the job immediately;
findings below that threshold still surface in the Security tab.

### vulture / refurb / perflint

No baseline; jobs are advisory. SARIF uploads still happen so
findings unify in Code Scanning. Promote a job to a hard gate by
removing the trailing `|| true` in the relevant step once the
existing backlog drops to zero.

## SARIF conversion

vulture, refurb, and perflint do not emit SARIF natively. The
workflow pipes their text output through `scripts/text_to_sarif.py`
which produces a SARIF 2.1.0 log suitable for
`github/codeql-action/upload-sarif`.

Adding a new tool is a matter of adding a regex + tool-meta entry in
`scripts/text_to_sarif.py` and a job block that mirrors one of the
advisory jobs.

## Local reproduction

```
# Semgrep CE
uv tool run semgrep scan --config p/python --config p/security-audit \
    --severity ERROR --severity WARNING --metrics off src/

# Trivy filesystem
trivy fs --severity HIGH,CRITICAL --ignore-unfixed .

# Trivy IaC
# .clusterfuzzlite/ is skipped: the fuzzing harness inherits the
# OSS-Fuzz base-builder image which runs as root by framework
# requirement and is not deployable infra.
trivy config --severity HIGH,CRITICAL --skip-dirs .clusterfuzzlite .

# vulture
uv tool run vulture src/ vulture_whitelist.py --min-confidence 70

# refurb
uv tool run refurb src/

# perflint (via pylint plugin)
uv tool run --from perflint pylint --load-plugins=perflint \
    --disable=all --enable=W8101,W8102,W8201,W8202,W8203,W8204,W8205,W8206,W8301 \
    src/
```

## Hardening

Every job runs `step-security/harden-runner` in audit mode and uses
SHA-pinned third-party actions. The workflow has no write
permissions beyond `security-events: write` for SARIF upload.
