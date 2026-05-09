# Session handoff

`bernstein handoff` moves a live session between surfaces — terminal,
web dashboard, chat bridge — without losing the active task or stream
tail. The source surface freezes and prints a short-lived token; the
destination presents the token and re-attaches.

```bash
# on the laptop terminal that started the run
bernstein handoff emit --session $SESSION_ID --from terminal

# token printed to stdout, e.g.
# h_3F8aQ2Kh9-Vb7c1dEs4Z6P-tGmRoYvLk

# on the dashboard host (or another terminal, or the chat bridge)
bernstein handoff claim h_3F8aQ2Kh9-Vb7c1dEs4Z6P-tGmRoYvLk
```

Tokens live for **5 minutes** and are **single-use**. The on-disk
registry is `.sdd/runtime/handoff_tokens.json`; expired entries are
swept on every load.

## Token format

Every token issued by `bernstein handoff emit` since `v1.10.1` is
prefixed with the literal string `h_`. A typical token looks like:

```text
h_3F8aQ2Kh9-Vb7c1dEs4Z6P-tGmRoYvLk
```

Anatomy:

| Segment | Length | Source |
|---------|--------|--------|
| `h_` prefix | 2 chars | Constant — used to recognise a handoff token in logs and to keep the token from starting with `-`. |
| Body | ~32 chars | URL-safe base64 from `secrets.token_urlsafe(24)`; alphabet is `A-Z a-z 0-9 - _`. |

Total length is therefore around 34 characters. Match against
`^h_[A-Za-z0-9_-]+$` if you need a regex.

### Why the prefix

`secrets.token_urlsafe()` occasionally produces a token whose first
character is `-`. Click — the CLI parser Bernstein uses — then misreads
`bernstein handoff claim -Vb7c1...` as the option `-V` followed by junk,
and the claim fails with an obscure `No such option` error. Roughly
1.5% of issued tokens hit this edge case. Forcing every token to start
with `h_` removes the ambiguity for click and gives operators a
single-glance way to spot a handoff token in a log line.

## Back-compat note for `v1.10.0` consumers

If you wrote any of the following against `v1.10.0` tokens, update for
`v1.10.1+`:

- A regex that did **not** include `h_` (e.g. `^[A-Za-z0-9_-]{32,}$`).
- A database column with a `CHECK` constraint on token shape, length,
  or starting character.
- A log scraper that parsed tokens by position from the
  `bernstein handoff emit` stdout.
- A test fixture hard-coding a `1.10.0`-shaped token.

The token is still URL-safe and printable, just two characters longer
and prefixed with `h_`. Old tokens issued by `1.10.0` are not
re-validated by `claim` — any token still in flight at the upgrade
boundary will fail closed; re-emit on the new version.

Implementation: `HandoffTokenStore.issue()` in
`src/bernstein/core/handoff/tokens.py`.

## Related

- [Voice control](voice-control.md) — `recap` / `show recap` voice
  commands wrap `bernstein recap`, which inherits the same session.
- [Autofix](autofix.md) — the `autofix attach` command uses the same
  resume-from-any-terminal handoff pattern for chat-control sessions.
