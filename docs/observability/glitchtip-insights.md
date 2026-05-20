# GlitchTip insights

Bernstein surfaces the read-side of the GlitchTip error sink so the
operator can see live issue counts, severity buckets, and the top
unresolved issues without leaving the terminal.

## TL;DR

- Runtime events flow to GlitchTip via `BERNSTEIN_GLITCHTIP_DSN`
  (existing wiring).
- Operators export `BERNSTEIN_GLITCHTIP_TOKEN` and run
  `bernstein doctor glitchtip` to pull the current state.
- A periodic nudge fires from `bernstein doctor --suggest-docs` when
  the API reports new unresolved issues since the last check.
- A daily workflow (`.github/workflows/glitchtip-insights.yml`)
  mirrors CRITICAL (fatal-level) GlitchTip issues into sticky GitHub
  issues labelled `glitchtip-alert`. The GitHub mirror auto-closes
  when the GlitchTip side resolves.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `BERNSTEIN_GLITCHTIP_TOKEN` | API token with org read scope. | (required for non-trivial output) |
| `BERNSTEIN_GLITCHTIP_DSN` | Runtime DSN for event submission. | (informational here; host derives the base URL if `BERNSTEIN_GLITCHTIP_BASE_URL` is unset) |
| `BERNSTEIN_GLITCHTIP_BASE_URL` | Override for the API base URL (for example `https://glitchtip.example.com`). | (no default; required when `BERNSTEIN_GLITCHTIP_DSN` is unset) |
| `BERNSTEIN_GLITCHTIP_ORG` | Organisation slug. | `bernstein` |
| `BERNSTEIN_GLITCHTIP_BASELINE` | Override for the baseline cache path. | `~/.local/share/bernstein/glitchtip-baseline.json` |

The shipped package hardcodes no observability backend host. Configure
the base URL at deployment time via `BERNSTEIN_GLITCHTIP_BASE_URL`, or
provide `BERNSTEIN_GLITCHTIP_DSN` and the base URL will be derived from
its host. When neither is configured, `bernstein doctor glitchtip`
soft-fails with a clear "not configured" message and exit code 0.

If `BERNSTEIN_GLITCHTIP_TOKEN` is missing, the doctor soft-fails with a
one-line hint and exit code 0. `BERNSTEIN_GLITCHTIP_DSN` is informational
only and does not affect the soft-fail. No operator workflow is interrupted.

## CLI

```
bernstein doctor glitchtip             # Rich tables
bernstein doctor glitchtip --json      # machine-readable snapshot
bernstein doctor glitchtip --top-n 10  # surface more issues
bernstein doctor glitchtip --no-baseline  # skip the baseline cache update
```

The Rich output contains four sections:

1. Header with the org slug and base URL.
2. 24h severity table (fatal / error / warning / info / debug / other).
3. 7-day trend sparkline bucketed by `firstSeen`.
4. Top-N unresolved table sorted by event count, then user count.

The JSON output mirrors the same fields under a single object:

```json
{
  "ok": true,
  "issues_24h": 2,
  "new_24h": 2,
  "severity_24h": {"fatal": 0, "error": 2, "warning": 0, ...},
  "trend_7d": [0, 0, 0, 0, 0, 0, 2],
  "top_unresolved": [{"short_id": "BERNSTEIN-2", ...}, ...]
}
```

## Baseline cache

The command writes a baseline snapshot at
`~/.local/share/bernstein/glitchtip-baseline.json` (overridable via
`BERNSTEIN_GLITCHTIP_BASELINE` or `XDG_DATA_HOME`). The file captures
the last observed 24h issue count, the top issue id, and the wall-clock
checkpoint. `bernstein doctor --suggest-docs` compares the current
state against this baseline and prints a single-line nudge when new
unresolved issues are detected.

## Daily workflow

`.github/workflows/glitchtip-insights.yml` runs at 06:30 UTC and on
`workflow_dispatch`. It:

- queries GlitchTip for fatal-level unresolved issues in the last 24h,
- creates a sticky GitHub issue per fresh fatal with label
  `glitchtip-alert`,
- updates the body of an existing mirror when the GlitchTip side is
  still active,
- reopens a closed mirror if the GlitchTip side returns,
- closes the mirror automatically when the GlitchTip issue moves to
  resolved.

The workflow soft-fails (exit 0) when `GLITCHTIP_API_TOKEN` is not
configured so fresh forks stay green.

## Secrets

The repo expects a single secret:

| Secret | Where minted | Used by |
|---|---|---|
| `GLITCHTIP_API_TOKEN` | GlitchTip org settings -> API tokens. | `glitchtip-insights.yml`. |

Set it with `gh secret set GLITCHTIP_API_TOKEN --repo <owner>/<repo>`
and feed the operator-minted value via stdin.
