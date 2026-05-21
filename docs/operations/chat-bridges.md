# Chat bridges

Bernstein ships bidirectional chat drivers so operators can drive a session,
approve tool calls, and watch streamed agent output from a messaging app
without keeping a terminal open. Bridges are configured per-platform via
`bernstein chat serve --platform=<name>`.

## Supported platforms

| Platform | Status | Optional extra | Tokens |
|----------|--------|---------------|--------|
| Telegram | Production | `pip install 'bernstein[telegram]'` | `BERNSTEIN_TELEGRAM_TOKEN` |
| Slack    | Production | `pip install 'bernstein[slack]'`    | `BERNSTEIN_SLACK_TOKEN` (bot) + `BERNSTEIN_SLACK_APP_TOKEN` (Socket Mode app) |
| Discord  | Stub only  | n/a                                 | n/a |

## Slack setup

1. Create a Slack app and enable Socket Mode.
2. Add the scopes `chat:write`, `commands`, `app_mentions:read`, plus any
   scopes your slash command surface needs.
3. Generate an app-level token (`xapp-...`) with the `connections:write`
   scope. Install the app to your workspace and copy the bot token
   (`xoxb-...`).
4. Map a `/bernstein` slash command in your Slack app configuration. The
   driver routes subcommands (`run`, `approve`, `reject`, `status`,
   `switch`, `stop`, `handoff`) from the text body of the slash payload.
5. Set the two env vars:

   ```sh
   export BERNSTEIN_SLACK_TOKEN=xoxb-...
   export BERNSTEIN_SLACK_APP_TOKEN=xapp-...
   ```

6. Start the bridge:

   ```sh
   bernstein chat serve --platform=slack
   ```

## What the driver guarantees

- **Slash dispatch and button decode.** `/bernstein run "..."` and the
  inline `Approve` / `Reject` buttons are routed through the same handler
  surface as the Telegram driver.
- **Edit debounce.** Streaming agent output collapses into one
  `chat.update` per channel per second so Slack's per-channel rate limit
  on `chat.update` stays comfortably out of reach.
- **Attested approvals.** Every approval resolution is appended to the
  HMAC-chained audit log as a `chat.slack.approval` event whose details
  cover `(approver, message_ts, decision, tool_call_hash, worktree_id)`.
  Replaying the chain reproduces the post-approval scheduler state
  byte-identically.
- **Worktree pinning.** An `/approve` for a worker bound to worktree
  `wt-a` cannot resolve a pending approval registered against a
  different worktree. Cross-worktree attempts log a
  `chat.slack.approval_rejected` audit entry and raise
  `CrossWorktreeApprovalError` so the bypass is visible to the operator.
- **Outbound message signing.** Every outbound chat message carries an
  Ed25519 detached signature over `(install_id, session_id,
  content_hash)`. A recipient with the install's public key can verify
  the message was not injected by another bernstein install
  impersonating the workspace.

## Verifying a Slack message

```python
from bernstein.core.chat.drivers.slack import verify_chat_signature

ok = verify_chat_signature(
    install_id="<the install id that posted>",
    session_id="<session id from the metadata envelope>",
    content="<text content of the message>",
    signature="<base64 signature from metadata.event_payload>",
    public_key_pem=open(".bernstein/keys/slack/slack-bridge.ed25519.pub", "rb").read(),
)
```

Returns `True` on cryptographic match, `False` on tampered content or a
foreign install public key.

## Missing SDK behaviour

`bernstein chat serve --platform=slack` raises a structured
`SlackDependencyError` when `slack-sdk` is not installed, with a
pointer to `pip install 'bernstein[slack]'`. Install the extra, or
switch to a platform whose SDK is already on the host.
