# Decision log

Audience: operators reconstructing *why* the router / profile / gate
picked what it picked, without re-reading four modules of routing code.

## Overview

Every routing / criterion-profile / gate-fire decision in Bernstein
writes one append-only JSONL record to `.sdd/runtime/decisions.jsonl`.

Source:

- `src/bernstein/core/observability/decision_log.py`
- `src/bernstein/cli/commands/decisions_cmd.py`

The log is append-only, concurrent-safe (in-process lock + append-mode
fd; cross-process safety from the kernel for the small line sizes
written here), and malformed-line tolerant on read.

## Record schema (`schema_version: 1`)

| Field | Use |
|-------|-----|
| `ts` | Float epoch seconds |
| `decision_id` | `dec-<uuid>` |
| `kind` | `model_route`, `mode_profile`, `criterion_profile`, `gate_fire` |
| `chosen` | Winner id |
| `alternatives` | `[{"id", "score", "reason"}]` (truncated at `MAX_ALTERNATIVES`) |
| `confidence` | `0.0 - 1.0` |
| `rationale` | Human-readable summary |
| `parent_decision_id` | Parent decision id (for chained routing) |
| `policy_path` | Ordered list of policies that fired |
| `winner_score` | Numeric score for the chosen alternative |
| `inputs` | Free-form `{"task_id": ..., ...}` |

## CLI

```text
bernstein decisions tail [-n N] [--path PATH]
bernstein decisions search [--kind KIND] [--since DURATION] [--path PATH]
```

- `--kind` filters to one decision kind (e.g. `model_route`).
- `--since` accepts `30s`, `15m`, `2h`, `1d`.
- `--path` overrides the default `.sdd/runtime/decisions.jsonl`.

Both commands render a Rich table with `ts`, `kind`, `chosen`,
`confidence`, and a trimmed rationale. Use redirection / shell tooling
on the raw JSONL when you need scriptable output.

## Examples

Last 50 routing decisions:

```bash
bernstein decisions tail -n 50
```

Look at every gate that fired in the last hour:

```bash
bernstein decisions search --kind gate_fire --since 1h
```

Pipe to `jq` for richer analysis:

```bash
jq -c 'select(.kind == "model_route")' .sdd/runtime/decisions.jsonl \
  | jq -s 'group_by(.chosen) | map({model: .[0].chosen, n: length})'
```

## Disabling

Set `BERNSTEIN_DECISION_LOG=0` in the orchestrator environment. The
single `record_decision` entry point becomes a no-op; routing paths
remain unchanged. The CLI subcommands keep working against any existing
log file.

## Bounded payloads

`alternatives` is truncated to `MAX_ALTERNATIVES` entries before
serialisation; a routing bug that explores 10,000 candidates cannot
fill the disk through this channel.

## Troubleshooting

**`bernstein decisions tail` is empty.** Either the log was disabled
via `BERNSTEIN_DECISION_LOG=0`, or no routing has happened yet in this
workdir. Run a small task first, then re-tail.

**Concurrent writers from a multi-process run.** Cross-process append
safety relies on the kernel's atomicity guarantee for sub-`PIPE_BUF`
writes. If you are sharing a log across hosts (NFS), wrap the runs
with separate log paths and merge offline.

**Malformed line warnings.** A partial line (machine killed mid-write)
will be skipped by `iter_records` with a debug log. Trim the trailing
broken line by hand if it bothers your downstream tooling.
