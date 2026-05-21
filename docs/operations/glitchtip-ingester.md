# GlitchTip event ingester

Audience: operators who want unresolved GlitchTip issues to surface as
regression eval cases in the existing incident-synthesis pipeline.

Companion to [`glitchtip-setup.md`](./glitchtip-setup.md) (outbound
events from Bernstein runs) and
[`glitchtip-insights.md`](../observability/glitchtip-insights.md) (the
read-side `bernstein doctor glitchtip` surface). This doc covers the
**inbound** direction: GlitchTip issues -> eval cases.

## TL;DR

| Step | Action |
|------|--------|
| 1 | Create an API token at `<glitchtip-host>/profile/auth-tokens` with `org:read project:read event:read issue:read` scopes |
| 2 | Export `GLITCHTIP_API_TOKEN` (and a base URL or DSN) into the environment |
| 3 | Run `python scripts/scrape_glitchtip_events.py` |
| 4 | The synthesizer's next `sync()` pass turns each JSON record into a P1 eval-case YAML |
| 5 | An operator reviews the emitted YAMLs in a PR (see the daily workflow) |

The scraper never contacts a hardcoded host. With no token set it exits
0 with a one-line notice, so a fresh checkout is never blocked.

## What it does

- Lists open `is:unresolved` issues across **all** projects in the org.
- Follows Sentry-protocol `Link: rel="next"` pagination (capped at 20 pages per run).
- Fetches each issue's latest event for exception type, message, and the deepest in-app stack frame.
- Reads the `environment`, `release`, and `server_name` event tags.
- Emits one JSON record per unique issue under `.sdd/reports/glitchtip_events/`.
- The `IncidentSynthesizer` reads those records on its next pass and writes one P1 eval-case YAML per record under `src/bernstein/eval/cases/incidents/`.

Each synthesized case carries:

- `source_incident: glitchtip-issue:<issue_id>`
- `severity: P1` (regression, warn-only by default)
- `owner: orchestrator`

## Record shape

One JSON object per issue:

```json
{
  "glitchtip_issue_id": "12345",
  "project_slug": "bernstein-orchestrator",
  "exception_type": "RuntimeError",
  "exception_value": "ci-verify shim raised on purpose",
  "top_frame_path": "src/bernstein/core/orchestration/conductor.py",
  "top_frame_line": 487,
  "first_seen": "2026-05-20T00:00:00Z",
  "last_seen": "2026-05-21T03:14:15Z",
  "event_count": 42,
  "environment": "production",
  "release": "bernstein@2.4.1",
  "title": "RuntimeError: ci-verify shim raised"
}
```

`title` is used only by the wiring-probe filter; it is not part of the
synthesized eval-case body.

## Env-var matrix

The scraper accepts two names for each setting. The `BERNSTEIN_*` names
(also used by `bernstein doctor glitchtip`) win when both are set; the
`GLITCHTIP_*` aliases match the GitHub Actions secret/var convention.

| Setting | Primary | Alias | Required | Default |
|---------|---------|-------|----------|---------|
| API token | `BERNSTEIN_GLITCHTIP_TOKEN` | `GLITCHTIP_API_TOKEN` | Yes (else exit 0) | none |
| Base URL | `BERNSTEIN_GLITCHTIP_BASE_URL` | `GLITCHTIP_BASE_URL` | Yes, or a DSN | none |
| Org slug | `BERNSTEIN_GLITCHTIP_ORG` | `GLITCHTIP_ORG_SLUG` | No | `bernstein` |

When no base URL is given, it is derived from the host of the first DSN
var that is set, in this order: `BERNSTEIN_GLITCHTIP_DSN`,
`BERNSTEIN_TELEMETRY_DSN`, `GLITCHTIP_DSN`. There is no hardcoded host:
when none of these resolve, the scraper exits 0.

## Token scopes

Create the token at `<glitchtip-host>/profile/auth-tokens`. Read-only is
enough for the scraper:

```text
org:read project:read event:read issue:read
```

`event:write issue:write` are only needed if you also resolve
administrative wiring-probe issues from the same token (the workflow's
smoke step does this).

## How dedup works

Re-runs are a pure no-op. Two dedup axes are merged before any record is
written:

1. **Scraper output.** Existing JSON files under the output directory,
   keyed on `glitchtip_issue_id`.
2. **Emitted YAML cases.** Existing `inc-*.yaml` files whose
   `source_incident` is `glitchtip-issue:<id>`.

The synthesizer adds a third axis at write time: the content hash of the
prompt (`inc-<sha1[:12]>`) and the `source_incident` slug. A record that
has already been promoted to a YAML case will not produce a second case
even if the scraper re-emits it.

## Wiring-probe allow-list

Administrative smoke issues seeded during initial wiring are filtered out
so they never become eval cases. The default list:

- `glitchtip insights wiring probe`
- `glitchtip smoke from operator finalisation`

Matching is case-insensitive and substring-based, so trivial variants
("... finalisation v2") are also filtered.

To add an entry, either:

- Edit `DEFAULT_WIRING_PROBE_ALLOW_LIST` in
  `scripts/scrape_glitchtip_events.py` (reviewed in the diff), or
- Pass `--wiring-probe "<title substring>"` on the command line
  (repeatable). Passing any `--wiring-probe` replaces the default list,
  so include the built-in entries too if you still want them filtered.

## Severity mapping (operator judgement)

All synthesized cases default to **P1** (warn-only). A runtime exception
captured in production is a regression but lacks the security-relevant
framing that gates merge. Promoting a specific exception class to P0
(blocking) is an explicit operator decision; the routing lives in
`_case_from_glitchtip_incident` in
`src/bernstein/eval/incident_synthesizer.py`. Change it there in a
reviewed PR rather than per-run.

## CLI

```text
python scripts/scrape_glitchtip_events.py \
    --out .sdd/reports/glitchtip_events \
    --cases-dir src/bernstein/eval/cases/incidents \
    [--wiring-probe "<title substring>"] \
    [--dry-run] [--verbose]
```

`--dry-run` prints the JSON records to stdout without writing files.

## Redaction

Every synthesized prompt is passed through the same PII/secret scanner
used by the rest of the incident pipeline
(`bernstein.core.security.pii_output_gate`). A case whose body still
trips the scanner after redaction is dropped, not written. The GlitchTip
hostname is never written into a record or a YAML body: only the issue
id, project slug, exception metadata, and frame path appear.

## Daily workflow

`.github/workflows/glitchtip-ingester.yml` runs the scraper on a daily
schedule and opens a PR with the emitted records for operator review. The
cron trigger ships disabled (`ENABLE_CRON: '0'`); flip it to `'1'` only
after a clean `workflow_dispatch` smoke run.
