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
| Discord  | Production | `pip install 'bernstein[discord]'`  | `BERNSTEIN_DISCORD_TOKEN` |

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

## Discord setup

1. Create a Discord application in the developer portal and add a bot
   to it. Enable the `applications.commands` scope on the bot invite
   URL plus any read/write scopes your slash commands need.
2. Copy the bot token and install the application to the target guild.
3. Register `/bernstein` (or your preferred name) as an application
   command in the Discord developer portal. The driver routes
   subcommand names (`run`, `approve`, `reject`, `status`, `switch`,
   `stop`, `handoff`) registered through `on_command`.
4. Set the env var:

   ```sh
   export BERNSTEIN_DISCORD_TOKEN=<bot token>
   ```

5. Start the bridge:

   ```sh
   bernstein chat serve --platform=discord
   ```

## Channel-scoped scheduling fence

The Discord driver maps each channel id to a *scheduler partition*
(canonical form: `discord:<channel_id>`). A pending approval is
registered against the partition of the channel the approval card
was posted to; a click that arrives in a different channel partition
is refused. Operators see two consequences:

- The bridge raises `ChannelPartitionMismatchError` on the calling
  goroutine so the orchestrator can fail closed.
- The HMAC-chained audit log gets a `chat.discord.approval_rejected`
  entry whose `details.reason` is `channel_partition_mismatch` and
  whose `pending_partition_id` / `request_partition_id` cover both
  sides of the mismatch.

The partition helper (`bernstein.core.orchestration.scheduler_partitions`)
is shared with the Slack driver so the on-disk partition labels stay
consistent across chat platforms.

## Verifying a Discord message

Discord does not surface custom message metadata to clients, so the
driver exposes the signed envelope via `bridge.last_signed_envelope()`
for downstream consumers (audit pipelines, fleet dashboards) that ship
the install's public key alongside the message body. The verification
shape is identical to Slack's:

```python
from bernstein.core.chat.drivers.discord import verify_chat_signature

envelope = bridge.last_signed_envelope()
ok = verify_chat_signature(
    install_id=envelope["install_id"],
    session_id=envelope["session_id"],
    content="<text content of the message>",
    signature=envelope["signature"],
    public_key_pem=open(".bernstein/keys/discord/discord-bridge.ed25519.pub", "rb").read(),
)
```

Returns `True` on cryptographic match, `False` on tampered content or
a foreign install public key.

## Missing SDK behaviour

`bernstein chat serve --platform=slack` raises a structured
`SlackDependencyError` when `slack-sdk` is not installed, with a
pointer to `pip install 'bernstein[slack]'`. `bernstein chat serve
--platform=discord` raises `DiscordDependencyError` with a pointer to
`pip install 'bernstein[discord]'` when `discord.py` is missing.
Install the extra, or switch to a platform whose SDK is already on
the host.
