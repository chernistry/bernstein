# Adapter rate-limit meter

The orchestrator wires a per-adapter `RateLimitMeter` into every CLI
adapter that extends `bernstein.adapters.base.CLIAdapter`. The meter
records rolling counters for upstream 429-class signals so
`bernstein status` and trace consumers have a single place to look
when a wave of agents starts saturating an upstream provider.

The meter only reports. It does not enforce request limits and it
does not auto-pause work. Operator decisions stay with the operator.

## What the meter tracks

| Field | Meaning |
|-------|---------|
| `requests_per_minute_target` | Operator-declared RPM target. `0` keeps the column unset. |
| `last_429_ts` | Unix timestamp of the most recent 429-class event. |
| `consecutive_429_count` | 429-class events since the last clean request. |
| `backoff_seconds_current` | Advisory exponential backoff value. |
| `hits_in_window` | 429-class events within the rolling window (default 5 minutes). |
| `last_error_code` | Provider-specific error label, when the adapter supplies one. |

## Surfaces

The meter is visible in three places:

1. `bernstein status` Rich panel - only renders when at least one
   meter has fired inside the rolling window.
2. `bernstein status --json` - emits a `rate_limit_meters` array with
   one snapshot per active adapter.
3. Trace consumers - `rate_limit.hit` events fold to one line per
   adapter via `bernstein.adapters.base.fold_rate_limit_events`:
   `"<adapter> hit 429 x<n> in last <window>"`.

## Lifecycle event

Every meter update calls `bernstein.adapters.base.record_rate_limit_hit`,
which fires a `rate_limit.hit` lifecycle event when an emit callback
is bound. Bind the callback to the orchestrator's `HookRegistry`
with `bernstein.core.lifecycle.hooks.bind_rate_limit_emit(registry)`.

The event payload carries:

```json
{
  "adapter": "claude",
  "provider": "anthropic",
  "error_code": "anthropic_429",
  "meter": { "...": "snapshot returned by RateLimitMeter.to_snapshot()" }
}
```

## Provider-specific 429 codes

The 4-6 most-used adapters are wired explicitly. Other adapters
inherit the default meter through the base class and update it via
`_probe_fast_exit` whenever an early non-zero exit looks like a
rate-limit signal.

| Adapter | Provider label | Wire-level signal |
|---------|----------------|-------------------|
| `claude` | `anthropic` | HTTP 429 with `error.type=rate_limit_error`; the CLI also surfaces "you've hit your limit" plus a "resets ..." banner. |
| `codex` | `openai` | HTTP 429 with `error.code` in `rate_limit_exceeded`, `insufficient_quota`. |
| `openai_agents` | `openai` | Same as Codex; the SDK forwards the upstream 429 verbatim. |
| `gemini` | `google_generative_language` | HTTP 429 with status `RESOURCE_EXHAUSTED`. |
| `cursor` | `cursor` | Cursor proxy returns HTTP 429 when its account-tier window is exhausted. |
| `copilot` | `github_copilot` | GitHub Copilot returns HTTP 429 once the account quota for chat completions is exhausted. |

The fast-exit probe in `CLIAdapter._probe_fast_exit` also matches a
broader set of textual cues that providers ship inside the CLI's
stdout or stderr: `rate limit`, `usage limit`, `quota exceeded`,
`too many requests`, `overloaded`, `hit your limit`, `limit exceeded`.
The probe records on the meter and re-raises a `RateLimitError` so
the spawn loop falls back through the existing rate-limit cooldown
path.

## Tuning

The rolling window defaults to 5 minutes
(`RATE_LIMIT_WINDOW_SECONDS = 300`). The exponential backoff curve
starts at 1 second and doubles per consecutive hit up to a 60-second
cap. Both values live in `bernstein.adapters.base` and are pure
data, so a downstream consumer can override them by re-importing the
constants.

## Out of scope

- Token-bucket scheduling. The meter records and reports; v2 may
  layer a scheduler.
- Cross-machine aggregation. Single-host view first.
- Auto-pause-and-resume. Operator decision.
- A new CLI subcommand. The status panel plus JSON output are the
  only surfaces.
