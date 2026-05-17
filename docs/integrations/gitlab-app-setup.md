# GitLab App Setup

Bernstein can receive GitLab webhooks (gitlab.com and self-managed) and
automatically create tasks from GitLab events (merge requests, notes,
pipeline failures).  This is the GitLab parity surface for the GitHub
App integration.

## Overview

The webhook handler lives at `POST /webhooks/gitlab` on the Bernstein
task server.  When GitLab sends a webhook, Bernstein:

1. Constant-time verifies the `X-Gitlab-Token` header against the
   configured shared secret.
2. Parses the `X-Gitlab-Event` header + JSON body.
3. Maps the event to one or more Bernstein tasks.
4. Creates the tasks in the task store.

## Event mapping

| GitLab event | Condition | Bernstein task |
|---|---|---|
| `Merge Request Hook` (action: `open`/`reopen`) | New / re-opened MR | Standard task, priority/role inferred from MR labels |
| `Pipeline Hook` (status: `failed`) | Failed pipeline | CI-fix task; retries escalate to `opus` after attempt 2 |
| `Note Hook` (note on MR/issue) | Slash command or actionable review language | `/bernstein <verb>` task, or fix task |

## Slash commands

`/bernstein <verb>` is supported on MR / issue notes:

| Verb | Resulting task |
|---|---|
| `fix [description]` | Priority-1 fix task, `backend` role |
| `plan [description]` | Planning task, `manager` role |
| `evolve [description]` | Upgrade proposal, `backend` role |
| `qa [description]` | QA verification task, `qa` role |
| `review [description]` | QA review task |

## Setup

### 1. Create the webhook secret

```bash
export GITLAB_WEBHOOK_TOKEN=$(openssl rand -hex 32)
```

For posting back to GitLab (pipeline statuses and MR cost notes):

```bash
export GITLAB_TOKEN=<personal-or-project-access-token>
```

For self-managed installs override the base URL:

```bash
export BERNSTEIN_GITLAB_URL=https://gitlab.example.com
```

The default is `https://gitlab.com`.

### 2. Configure the webhook in GitLab

In your project: **Settings > Webhooks > Add new webhook**.

- **URL**: `https://<your-server>/webhooks/gitlab`
- **Secret token**: paste the same value as `GITLAB_WEBHOOK_TOKEN`.
- **Trigger**: enable *Merge request events*, *Pipeline events*,
  *Comments* (Notes).
- **SSL verification**: keep enabled in production.

### 3. Start the server

```bash
bernstein
```

GitLab will deliver to `/webhooks/gitlab`; Bernstein verifies the
token via `hmac.compare_digest` (constant time) and routes events.

## Local development

Use a tunneling tool (e.g. `ngrok`, `cloudflared`) to expose your
server.  Set the public URL as the webhook target in GitLab's project
settings.

## Self-managed GitLab

For self-managed installs:

* Set `BERNSTEIN_GITLAB_URL` to the on-prem instance base URL.
* Use a project / group access token (with `api` scope) instead of a
  personal access token if you want least-privilege.
* The same `/webhooks/gitlab` endpoint and `X-Gitlab-Token` header
  apply — GitLab's webhook surface is identical between gitlab.com and
  self-managed.

## Secret rotation

The webhook token rotates the same way as any shared secret:

1. Generate a new value: `openssl rand -hex 32`.
2. In GitLab: update the project's webhook *Secret token*.
3. On the server: set `GITLAB_WEBHOOK_TOKEN` and restart Bernstein.
4. Confirm with a test delivery from the GitLab webhook page.

Between steps 2 and 3 you'll get 401s; perform during a brief
maintenance window.

## Label conventions

Priority mapping (mirrors GitHub):

* `bug`, `critical`, `security` -- priority 1 (highest)
* `enhancement`, `feature` -- priority 2
* `docs`, `documentation`, `chore` -- priority 3

Role mapping:

* `backend`, `frontend`, `qa`, `security`, `docs` -- mapped directly
  to Bernstein roles.

## Architecture

```
GitLab --> POST /webhooks/gitlab --> verify_token()       (constant-time)
                                 --> parse_webhook()
                                 --> mapper (merge_request_to_tasks /
                                             pipeline_to_tasks /
                                             note_to_task)
                                 --> store.create()
```

Source code: `src/bernstein/gitlab_app/`.

| Module | Purpose |
|---|---|
| `app.py` | Token-based auth + base-URL config (`BERNSTEIN_GITLAB_URL`) |
| `webhooks.py` | `X-Gitlab-Token` constant-time verify + event parsing |
| `mapper.py` | GitLab event → TriggerEvent normalisation |
| `slash_commands.py` | `/bernstein <verb>` parser for MR / issue notes |
| `pipelines.py` | Commit-status API: MR pipeline status from agent runs |
| `ci_router.py` | Trace fetch + parse + blame for failed pipelines |
| `cost_reporter.py` | MR note: posts agent run cost summary |
