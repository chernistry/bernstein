# bernstein doctor

`bernstein doctor` is the single-command checklist for diagnosing a
broken Bernstein installation before opening an issue. The legacy
`bernstein doctor` runs the same checks it always did; the new
`bernstein doctor extended` subcommand layers on three additional
categories: adapter binaries, network reachability, and CI/sandbox
context.

## Quick start

```bash
bernstein doctor                                      # legacy report
bernstein doctor extended                             # full extended report
bernstein doctor extended --json                      # machine-readable output
bernstein doctor extended --adapter claude --provider anthropic
BERNSTEIN_OFFLINE=1 bernstein doctor extended         # skip every network probe
```

Exit code: `1` if any check fails, `0` otherwise. Warnings do not
trigger a non-zero exit but are echoed to stderr.

## Check categories

| Category        | Source module                                         | Honors                |
|-----------------|-------------------------------------------------------|-----------------------|
| `installation`  | `bernstein.cli.install_check` (preserved unchanged)   | n/a                   |
| `adapter`       | `bernstein.cli.doctor.adapter_checks`                 | `bernstein.yaml`      |
| `network`       | `bernstein.cli.doctor.network_checks`                 | `BERNSTEIN_OFFLINE=1` |
| `environment`   | `bernstein.cli.doctor.environment_checks`             | env vars + file probes|

### Installation checks

Unchanged from before:

- duplicate `bernstein` binaries on `PATH`
- installed package version
- virtual-environment isolation

### Adapter binary checks

For each adapter declared in `bernstein.yaml`:

1. resolve the binary via `shutil.which`
2. spawn `<binary> --version` with a 5-second timeout
3. report PATH presence, version string, exit code, or hang

Status mapping:

| Outcome                        | Status |
|--------------------------------|--------|
| binary present, version capture| `ok`   |
| `--version` hangs              | `warn` |
| `--version` exits non-zero     | `warn` |
| binary not on `PATH`           | `fail` |

### Network reachability

Each provider maps to a single hostname. The check opens a TCP
connection to port 443 with a 2-second timeout.

| Provider     | Host                                       |
|--------------|--------------------------------------------|
| anthropic    | `api.anthropic.com`                        |
| openai       | `api.openai.com`                           |
| google       | `generativelanguage.googleapis.com`        |
| openrouter   | `openrouter.ai`                            |
| groq         | `api.groq.com`                             |
| mistral      | `api.mistral.ai`                           |
| deepseek     | `api.deepseek.com`                         |

Set `BERNSTEIN_OFFLINE=1` to skip every network probe. The doctor
report shows a single compact `network:*` skip row in that case.

DNS-resolution failures, refused connections, and timeouts are reported
distinctly so the operator can tell a broken resolver from blocked
egress.

### Environment / sandbox detection

Detection combines environment variables with light-weight file
probes. Detected environments are surfaced in the report so issue
reporters can include the context automatically.

| Marker                        | Source                              |
|-------------------------------|-------------------------------------|
| `GITHUB_ACTIONS=true`         | GitHub Actions                      |
| `GITLAB_CI=true`              | GitLab CI                           |
| `BUILDKITE=true`              | Buildkite                           |
| `CIRCLECI=true`               | CircleCI                            |
| `JENKINS_URL`                 | Jenkins                             |
| `DEVCONTAINER=true`           | VS Code devcontainer                |
| `REMOTE_CONTAINERS=true`      | VS Code devcontainer (remote)       |
| `INVOCATION_ID`               | systemd-run                         |
| `/.dockerenv` exists          | Docker                              |
| `CI=true` (no specific match) | Generic CI                          |

Multiple markers can match simultaneously (for example, Docker inside
GitHub Actions). The renderer suppresses the generic `CI` row when a
more specific environment matched.

## Example output

```
                    Bernstein Doctor
─────────────────────────────────────────────────────────────────
 Check               Category      Status   Detail                Remediation
 install:bernstein   installation  ✓ OK     v2.0.1
 adapter:claude      adapter       ✓ OK     /usr/local/bin/claude -> claude-code 2.1.5
 adapter:codex       adapter       ✗ FAIL   Binary `codex` not in PATH   Install via vendor instructions
 network:anthropic   network       ✓ OK     reachable: api.anthropic.com:443
 network:openai      network       ✓ OK     reachable: api.openai.com:443
 env:github-actions  environment   ✓ OK     GitHub Actions detected
─────────────────────────────────────────────────────────────────
 4 OK   0 WARN   1 FAIL   0 SKIP
```

## Programmatic API

```python
import asyncio
from bernstein.cli.doctor import run_all, render_report, exit_code_for

results = asyncio.run(run_all())
render_report(results)
raise SystemExit(exit_code_for(results))
```

Every result is a frozen `DoctorResult(name, category, status, detail,
remediation)` dataclass. `category` is one of `installation`,
`adapter`, `network`, `environment`. `status` is one of `ok`, `warn`,
`fail`, `skip`.

## Troubleshooting matrix

| Symptom                                         | Likely fix                                                   |
|-------------------------------------------------|--------------------------------------------------------------|
| `adapter:* fail - Binary not in PATH`           | install the vendor CLI or remove the adapter from config     |
| `adapter:* warn - exited 2`                     | upgrade the binary; check `<bin> --version` manually         |
| `adapter:* warn - timed out after 5s`           | the binary is wedged; kill stale processes                   |
| `network:* fail - DNS lookup failed`            | fix `/etc/resolv.conf` or your DNS resolver                  |
| `network:* fail - connection refused`           | check proxy or firewall egress rules                         |
| `network:* skip - BERNSTEIN_OFFLINE=1`          | expected when air-gapped; clear the env var to re-enable     |
| `env:docker` with no other CI markers           | host is a vanilla Docker container; include in bug reports   |
