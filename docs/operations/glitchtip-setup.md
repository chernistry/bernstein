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
| 4 | Export `GLITCHTIP_DSN=<dsn>` into every Bernstein process |
| 5 | Trigger a controlled error and confirm the issue lands in the UI |

## Env var

The Bernstein CLI reads exactly one env var at startup:

```text
GLITCHTIP_DSN=https://<public-key>@<host>/<project-id>
```

Defined in `src/bernstein/cli/main.py::_init_error_telemetry`.  When the
var is unset or empty, the helper is a no-op and `sentry-sdk` is never
imported -- minimal installs pay zero overhead.

`SENTRY_DSN` is **not** read.  Bernstein deliberately uses a
project-specific env var to keep operator-managed error sinks distinct
from any third-party Sentry wiring an embedded library might do.

## Provisioning a project

1. Log in to the GlitchTip UI (operator-managed admin credentials).
2. `Organization -> New Project`, pick **Python** as the platform.
3. Open the project's **Client Keys (DSN)** page and copy the public DSN.
   Format: `https://<32-hex-public-key>@<host>/<numeric-project-id>`.
4. Store the DSN as the `GLITCHTIP_DSN` secret in every deploy surface
   that runs Bernstein:

   * GitHub Actions: `Settings -> Secrets and variables -> Actions ->
     New repository secret`, name `GLITCHTIP_DSN`.  The
     `bernstein-deploy.yml` workflow passes it through to the deploy
     target.
   * systemd: drop into the unit's `EnvironmentFile=` or via
     `Environment=GLITCHTIP_DSN=...`.
   * Container: pass via `--env GLITCHTIP_DSN=...` or compose `env_file`.
   * Local operator workstation: `export GLITCHTIP_DSN=...` in the
     shell that runs `bernstein`.

## Verifying events flow

After exporting the DSN, run a controlled capture to confirm ingestion:

```bash
export GLITCHTIP_DSN='https://<public-key>@<host>/<project-id>'
python -c "
import os, bernstein.cli.main  # triggers _init_error_telemetry
import sentry_sdk
sentry_sdk.capture_message('bernstein glitchtip smoke', level='info')
sentry_sdk.flush(timeout=5)
"
```

Then open the GlitchTip UI -> Issues for the project.  The
`bernstein glitchtip smoke` issue should appear within a few seconds.

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
2. Update `GLITCHTIP_DSN` in every deploy surface listed above.
3. Restart Bernstein processes so they pick up the new DSN at import time.
4. Revoke the old key once traffic on it has stopped.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| No events in UI | Env var not exported in the process | Confirm with `printenv GLITCHTIP_DSN` inside the running process namespace |
| `ImportError` swallowed silently | `sentry-sdk` not installed | Reinstall with the extra: `pip install 'bernstein[observability]'` |
| `429 Too Many Requests` from sink | Per-project throttle in GlitchTip | Raise `eventThrottleRate` on the project, or bound `before_send` |
| DSN works locally, not on VPS | Egress firewall blocks the GlitchTip host | Whitelist the sink host in the VPS egress policy |
