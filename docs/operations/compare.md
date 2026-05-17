# Compare (side-by-side adapter A/B)

Audience: operators picking between adapters (Claude vs Codex vs Aider
vs Gemini) on the same task, without burning two manual runs.

## Overview

`bernstein compare` runs the same task spec in parallel against up to
**four** adapters in isolated per-adapter worktrees, diffs the produced
changes against the baseline workspace, and writes a JSON sidecar plus
a Markdown summary.

The single-adapter `bernstein run` path is untouched. The compare
runner is a separate, deterministic harness.

Source:

- `src/bernstein/cli/commands/compare_cmd.py`
- `src/bernstein/core/orchestration/compare_runner.py`
- `src/bernstein/eval/telemetry.py` (adds `compare_run_id`)

## CLI

```text
bernstein compare SPEC_PATH --adapters NAME[,NAME...]
  [--workspace DIR]      baseline snapshot; defaults to cwd
  [--role NAME]          role applied identically to all adapters; default backend
  [--seed N]             deterministic seed forwarded to adapters; default 0
  [--keep-worktrees]     keep per-adapter worktrees on disk after the run
  [--traces-dir DIR]     override JSON sidecar dir; default .sdd/traces
  [--no-sidecar]         skip writing the JSON sidecar
```

Cap is 4 adapters per run. Asking for more exits with `2` and an
explicit error. Duplicates are rejected. The degenerate 1-adapter case
is allowed for harness symmetry.

Adapter spawn is parallel (one thread per adapter); the runner itself
stays single-threaded for clean cleanup semantics.

## Outputs

- **stdout** - Markdown summary table (one row per adapter) plus
  per-file diff blocks.
- **JSON sidecar** - `.sdd/traces/compare-<id>.json` (overridable via
  `--traces-dir`); skipped with `--no-sidecar`.
- **Telemetry** - `AgentTelemetry` rows carry `compare_run_id` so
  downstream eval ingestion can group runs.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | At least one adapter exited cleanly |
| Non-zero | All adapters failed (`exit_code != 0` for every adapter) |

Per-adapter failures are always rendered in the summary regardless of
the overall exit code.

## Examples

Two-adapter A/B:

```bash
bernstein compare ./task-spec.md --adapters claude,codex
```

Four-way bake-off, keep worktrees for offline inspection:

```bash
bernstein compare ./task-spec.md \
  --adapters claude,codex,gemini,aider \
  --keep-worktrees
```

Throwaway smoke run, no sidecar:

```bash
bernstein compare ./task-spec.md --adapters claude --no-sidecar
```

## Worktree hygiene

Each adapter gets its own worktree under
`.sdd/runtime/compare/<compare_run_id>/<adapter>/`. The runner clones
the baseline workspace into the worktree with `shutil.copytree`, runs
the adapter, computes a unified diff against baseline, and then
removes the worktree unless `--keep-worktrees` is set.

`BERNSTEIN_TRACES_DIR` is honoured for the sidecar location.

## Troubleshooting

**`unknown adapter: X`.** The adapter is not registered. Run
`bernstein agents` to list registered adapters; install / register the
missing one or drop it from `--adapters`.

**`--adapters cap is 4; got N`.** Trim the list. The cap is hard.

**Adapter binary missing.** The runner does not crash; instead it
reports a per-adapter `error` row in the summary so the comparison
still shows the rest of the bake-off.
