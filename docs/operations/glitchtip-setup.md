# GlitchTip setup

Audience: operators wiring Bernstein into a GlitchTip (or any
Sentry-protocol-compatible) error sink and confirming events flow end to
end.

Companion to [`observability.md`](./observability.md), which documents the
broader telemetry contract.  This doc focuses on the operator-facing
mechanics: project provisioning, DSN distribution, runtime export, and
event verification.

## TL;DR

| Step | Action |
|------|--------|
| 1 | Create an organization + project in the GlitchTip UI |
| 2 | Copy the project DSN (`Settings -> Projects -> <name> -> Client Keys`) |
| 3 | Store the DSN in the deploy environment (GH secret, systemd unit, container env) |
| 4 | Export `BERNSTEIN_TELEMETRY_DSN=<dsn>` into every Bernstein process |
| 5 | Trigger a controlled error and confirm the issue lands in the UI |

## Env vars

The Bernstein CLI reads its DSN from a small, ordered set of env vars at
startup:

```text
BERNSTEIN_TELEMETRY_DSN=https://<public-key>@<host>/<project-id>   # canonical
GLITCHTIP_DSN=https://<public-key>@<host>/<project-id>             # legacy alias
```

Resolved in `src/bernstein/cli/main.py::_init_error_telemetry`. The
canonical name is `BERNSTEIN_TELEMETRY_DSN`: a host-agnostic,
project-specific variable documented in
`docs/observability/side-channel.md`. `GLITCHTIP_DSN` is honoured as a
deprecated fallback for deployments wired before the rename; new
deployments should use `BERNSTEIN_TELEMETRY_DSN`.

When neither var is set, the helper is a no-op and `sentry-sdk` is never
imported. Minimal installs pay zero overhead.

The plain `SENTRY_DSN` is **not** read. Bernstein deliberately uses a
project-specific env var so an operator-managed error sink stays
distinct from any third-party Sentry wiring an embedded library might
do.

## Provisioning a project

1. Log in to the GlitchTip UI (operator-managed admin credentials).
2. `Organization -> New Project`, pick **Python** as the platform.
3. Open the project's **Client Keys (DSN)** page and copy the public DSN.
   Format: `https://<32-hex-public-key>@<host>/<numeric-project-id>`.
4. Store the DSN as the `BERNSTEIN_TELEMETRY_DSN` secret in every deploy
   surface that runs Bernstein:

   * GitHub Actions: `Settings -> Secrets and variables -> Actions ->
     New repository secret`, name `BERNSTEIN_TELEMETRY_DSN`. The
     `bernstein-deploy.yml` workflow passes it through to the deploy
     target.
   * systemd: drop into the unit's `EnvironmentFile=` or via
     `Environment=BERNSTEIN_TELEMETRY_DSN=...`.
   * Container: pass via `--env BERNSTEIN_TELEMETRY_DSN=...` or compose
     `env_file`.
   * Local operator workstation: `export BERNSTEIN_TELEMETRY_DSN=...` in
     the shell that runs `bernstein`.

## Workflow vars (CI insights sweep)

The `.github/workflows/glitchtip-insights.yml` workflow performs a daily
sweep of fatal-level GlitchTip issues and mirrors them as GitHub issues.
It needs two pieces of configuration on the repository:

| Kind | Name | Purpose |
|------|------|---------|
| secret | `GLITCHTIP_API_TOKEN` | Bearer token for the GlitchTip API |
| var    | `GLITCHTIP_BASE_URL`  | Base URL of the GlitchTip instance, e.g. `https://errors.example.com` |

Both are optional: the workflow short-circuits with a notice when
either is empty so forks stay green until the operator wires them.
Set the variable under `Settings -> Secrets and variables -> Actions ->
Variables -> New repository variable`.

## Verifying events flow

After exporting the DSN, run a controlled capture to confirm ingestion:

```bash
export BERNSTEIN_TELEMETRY_DSN='https://<public-key>@<host>/<project-id>'
python -c "
import bernstein.cli.main  # triggers _init_error_telemetry
import sentry_sdk
sentry_sdk.capture_message('bernstein glitchtip smoke', level='info')
sentry_sdk.flush(timeout=5)
"
```

Then open the GlitchTip UI -> Issues for the project. The
`bernstein glitchtip smoke` issue should appear within a few seconds.

To verify CLI-side exception capture, the first-run guard at
`src/bernstein/cli/first_run_guard.py::handle_first_run_exception` calls
`sentry_sdk.capture_exception` explicitly before converting a top-level
exception into a Rich hint panel. The default `sys.excepthook` never
sees these exceptions, so the explicit call is required to keep
GlitchTip in the loop.

To verify via the API (auth token from `Profile -> Auth Tokens`):

```bash
curl -s -H "Authorization: Bearer $GLITCHTIP_API_TOKEN" \
  https://<host>/api/0/projects/<org-slug>/<project-slug>/issues/ \
  | jq '.[].title'
```

## Rotating the DSN

GlitchTip lets the operator add/revoke client keys without re-provisioning
the project.  To rotate:

1. `Project Settings -> Client Keys -> New Client Key`.
2. Update `BERNSTEIN_TELEMETRY_DSN` in every deploy surface listed above.
3. Restart Bernstein processes so they pick up the new DSN at import time.
4. Revoke the old key once traffic on it has stopped.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No events in UI | Env var not exported in the process | Confirm with `printenv BERNSTEIN_TELEMETRY_DSN` inside the running process namespace |
| `ImportError` swallowed silently | `sentry-sdk` not installed | Reinstall with the extra: `pip install 'bernstein[observability]'` |
| `429 Too Many Requests` from sink | Per-project throttle in GlitchTip | Raise `eventThrottleRate` on the project, or bound `before_send` |
| DSN works locally, not on VPS | Egress firewall blocks the GlitchTip host | Whitelist the sink host in the VPS egress policy |
| Workflow `glitchtip-insights` skipped with notice | `GLITCHTIP_BASE_URL` var or `GLITCHTIP_API_TOKEN` secret not set | Configure both on the repository as described above |
