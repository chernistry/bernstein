# Maintainer-share telemetry (RFC #1719 foundation)

Bernstein already exposes a portable, operator-controlled telemetry pipeline
(see [side-channel.md](./side-channel.md)). That pipeline is the operator's:
the operator picks the backend, points the DSN at it, and reviews the stream.

This page documents a separate, additive consent surface: the
`share_with_maintainer` flag introduced by the RFC #1719 foundation. The flag
gates an opt-in path to a maintainer-operated endpoint. No endpoint URL is
baked into the package.

## TL;DR

- Default state is off. With the flag unset, nothing is ever sent.
- One TOML file controls consent:
  `$XDG_CONFIG_HOME/bernstein/telemetry.toml` (falls back to
  `~/.config/bernstein/telemetry.toml`).
- Three CLI subcommands manage the surface:
  - `bernstein telemetry enable --share-with-maintainer` to opt in.
  - `bernstein telemetry disable` to revert.
  - `bernstein telemetry tail [-n N]` to audit the stream offline.
- One env var beats the file: `BERNSTEIN_TELEMETRY_SHARE=0` always disables.
- The share sink also requires `BERNSTEIN_TELEMETRY_SHARE_ENDPOINT`.
- `DO_NOT_TRACK=1` always wins, regardless of the file or any env var.

## How consent flows

1. Operator runs `bernstein telemetry enable --share-with-maintainer`.
2. The CLI prints the full event schema and the redaction list. Nothing is
   written yet.
3. The CLI asks for confirmation. If the operator declines, the flag stays
   unset and the path stays unreachable.
4. On confirmation, the CLI writes `share_with_maintainer = true` to the
   TOML file and prints the path.

To revoke at any time, run `bernstein telemetry disable`. The TOML is
rewritten with `share_with_maintainer = false` and the path goes back to
unreachable on the next process start.

## Configuration precedence

The flag is resolved from the highest available signal:

| Layer | Signal | Effect |
|---|---|---|
| 1 | `DO_NOT_TRACK=1` env var | Forces off (universal W3C opt-out). |
| 2 | `BERNSTEIN_TELEMETRY_SHARE` env var | `0` / `false` / `no` / `off` / `""` force off; anything else forces on. |
| 3 | TOML file `share_with_maintainer` | The persisted operator choice. |
| 4 | Default | Off. |

`bernstein telemetry status` prints the resolved value and the layer that
won, so operators can diagnose surprising state in one command.

## Endpoint configuration

The maintainer-share sink is inert unless both values are present:

| Setting | Required value |
|---|---|
| `share_with_maintainer` | `true` from the TOML file or `BERNSTEIN_TELEMETRY_SHARE=1` |
| `BERNSTEIN_TELEMETRY_SHARE_ENDPOINT` | An HTTPS receiver URL supplied by the runtime environment |

`BERNSTEIN_TELEMETRY_SHARE_ENDPOINT` has no default. Setting the endpoint
without consent sends nothing. Setting consent without the endpoint sends
nothing.

## What gets sent

The event schema is the closed taxonomy already defined in
`src/bernstein/core/telemetry/events.py`. Field-by-field:

| Event | Fields |
|---|---|
| `install_completed` | `os`, `py_version`, `install_method`, `bernstein_version` |
| `first_run_started` | `time_since_install_seconds` |
| `first_run_completed` | `ok`, `duration_ms`, `error_category` |
| `command_invoked` | `name_only`, `bernstein_version` |
| `daily_active` | `day_iso` (ISO-8601 UTC date) |

Every event is wrapped in an envelope that adds `schema_version`,
`install_id`, and `timestamp`. The install id is the opaque per-install
fingerprint documented in `core/telemetry/install_id.py`.

The maintainer-share request body is exactly the same serialized event JSON
line written to the local audit queue. The detached Ed25519 receipt is carried
in HTTP headers:

| Header | Value |
|---|---|
| `x-bernstein-telemetry-agent-id` | `install:<install_id>` |
| `x-bernstein-telemetry-kid` | Receipt key id |
| `x-bernstein-telemetry-jws` | Detached JWS over the request body |
| `x-bernstein-telemetry-public-key-pem-b64` | Base64url public key PEM |
| `x-bernstein-telemetry-receipt-version` | Receipt format version |

The receipt key is generated only after both consent and endpoint gates are
enabled. The private key is stored locally at
`~/.bernstein/telemetry-share-key.pem`.

## Redaction list

The schema is the redaction list: nothing outside the table above is ever
collected. The boundary explicitly drops:

- **File paths**: any path-shaped value is replaced with a stable hash before
  it can enter a field. No raw paths are ever serialised.
- **Agent output**: rendered text never enters the pipeline.
- **Diff bytes**: source patches are not part of the schema.
- **Tool-call args**: only the bare command name is emitted; arguments are
  stripped.
- **Prompts**: user-authored prompts never reach the boundary.
- **Secrets**: env vars and credentials are not part of the schema.

The schema is also guarded by a build-time test
(`tests/unit/telemetry/test_event_schema_guard.py`): any new field that lacks
either an entry in the allowlist of safe primitive fields or an explicit
redaction entry fails the test. Field renames or removals are a major-version
bump on the event schema; operators can pin a schema version they have
reviewed.

## Auditing the stream offline

`bernstein telemetry tail` reads the in-process side-channel preview ring
buffer and prints the most recent rendered events, one JSON object per line,
oldest first. The buffer is populated at the boundary, so the output reflects
what would be sent, regardless of whether the backend is reachable. Use this
to audit the stream before deciding whether to flip the flag.

```text
$ bernstein telemetry tail -n 5
{"event_id":"...","level":"info","logger":"bernstein.run", ...}
```

## Revoking consent

Three equivalent revocations:

- `bernstein telemetry disable` writes `share_with_maintainer = false`.
- `export BERNSTEIN_TELEMETRY_SHARE=0` forces off for the current process.
- `export DO_NOT_TRACK=1` forces off for every Bernstein process and matches
  the universal W3C signal.

The operator-controlled side channel (`BERNSTEIN_TELEMETRY_DSN`) is
unaffected by any of the above. Operators who run their own backend never
need to touch the share flag.

## What is not included

- It does not define a maintainer endpoint URL.
- It does not flip the flag for any operator. The default stays off.
- It does not send fields outside the closed telemetry schema.
