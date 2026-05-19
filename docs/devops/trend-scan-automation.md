# Trend scan automation

A scheduled job that ingests upstream dependency-relevant signals into the
orchestrator backlog directory as a markdown rollup. No tickets are filed
automatically; the operator reviews the rollup and runs `bernstein backlog
new` for the rows that warrant a ticket.

## What it does

1. Iterates configured source specs (each with required + boost +
   negative keywords).
2. Calls an injected fetcher per source (a subprocess command, or an
   offline stub for testing).
3. Scores candidates with a deterministic keyword-overlap function,
   normalised by document length.
4. Runs gap analysis against `.sdd/backlog/` and (optionally) a list of
   recently-closed issue keywords, classifying each row as `new`,
   `duplicate`, or `recently-closed`.
5. Writes a markdown rollup plus a sibling JSON file.

## CLI

```
bernstein trend-scan run \
  --tier all \
  --rollup-dir .sdd/trend-scan \
  --backlog-dir .sdd/backlog
```

Common flags:

| Flag | Purpose |
| --- | --- |
| `--tier` | Restrict to one tier (`all`, `1`, `2`, `3`). |
| `--output` | Override the rollup file path. |
| `--sources` | JSON file overriding the default source specs. |
| `--fetcher-cmd` | External fetcher executable. |
| `--offline-stub` | Force the no-op fetcher (no network). |
| `--max-per-source` | Cap candidates surfaced per source (default 5). |

### Source spec format

```json
[
  {
    "name": "python-release-notes",
    "tier": 1,
    "keywords": ["python", "release"],
    "boost_keywords": ["security", "deprecation"],
    "negative_keywords": ["draft"],
    "min_score": 0.5
  }
]
```

### Wiring a real fetcher

The CLI does not perform network I/O itself. To feed live data, point
`--fetcher-cmd` at an executable. It is invoked once per source:

```
<fetcher-cmd> <source_name> <tier>
```

The executable must write one JSON object per line on stdout with the
shape:

```json
{"title": "...", "url": "...", "ts": "2026-05-19T00:00:00Z", "body": "..."}
```

Malformed lines are skipped with a warning. Non-zero exit treats the
source as empty for that run.

## Scheduled workflow

`.github/workflows/trend-scan.yml` runs the CLI on `workflow_dispatch` by
default. To enable the weekly schedule, uncomment the `schedule:` block in
that file. The job uploads the rollup as a workflow artifact for operator
review; it does not commit or open issues.

## Operator workflow

1. Open the latest workflow run, download `trend-scan-rollup`.
2. Skim the table; ignore `duplicate` and `recently-closed` rows unless
   context has changed.
3. For each `new` row that warrants action, run `bernstein backlog new`
   and paste the relevant URL plus a one-line problem statement.

## Tests

`tests/unit/devops/test_trend_scan.py` covers scoring, filtering, gap
analysis, the end-to-end `run_scan` entry point, and a CLI smoke test
using `click.testing.CliRunner`. All tests use the offline stub or
injected fetchers; nothing in the test suite touches the network.
