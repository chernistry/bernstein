# Worktrees CLI (`bernstein worktrees`)

Audience: operators with a `.sdd/runtime/worktrees/` (or legacy
`.sdd/worktrees/`) tree that has accumulated orphans after kills,
crashes, or aborted experiments.

## Overview

`bernstein worktrees` inspects and reaps worktrees the orchestrator
left behind. The classifier in
`src/bernstein/core/worktrees/classifier.py` is the single source of
truth for state. The CLI module
(`src/bernstein/cli/commands/worktrees_cmd.py`) only handles I/O:
rendering the table, holding the GC lock, prompting the operator, and
emitting the `worktree.gc` lifecycle event for plugins.

The tool honours both the spec layout (`.sdd/runtime/worktrees/`) and
the legacy layout (`.sdd/worktrees/`) the `WorktreeManager` currently
produces.

## State machine

| State | Rule |
|-------|------|
| `active` | Task record exists at `.sdd/runtime/pids/<sid>.json` AND `os.kill(pid, 0)` succeeds. |
| `orphan` | Directory exists but no task record. |
| `stale` | Task record exists but PID is dead AND last trace mtime > 24h ago. |
| `corrupt` | Directory exists but the `.git` anchor is missing. |

Priority on conflicts: `corrupt > orphan > stale > active`. A dead PID
with a fresh trace stays `active` to avoid racing a restart.

## CLI

```text
bernstein worktrees list   [--workdir DIR] [--json]
bernstein worktrees gc     [--workdir DIR] [--yes] [--dry-run]
```

- `list` - tabular dump with path, task id, state, age, size, PID. Use
  `--json` for scripting.
- `gc` - reap non-`active` worktrees. `--yes` skips the confirmation
  prompt; `--dry-run` prints what would be reaped without touching
  disk.

## GC lock

A single-file lock at `.sdd/runtime/worktree-gc.lock` is held via
`O_EXCL` for the duration of `gc`. The lock is released on exception.

Exit code `2` indicates a lock collision (another `gc` is in flight or
the lock file is stale).

## Lifecycle event

The CLI emits `worktree.gc` after each reap (or, with `--dry-run`,
once per would-be reap). Plugins can hook this event; the env keys
exposed to handlers are `BERNSTEIN_WORKTREE_GC_*`.

## TUI integration

The TUI's `WorktreeListPanel` refreshes the same classifier output
every 10 seconds and uses `count_reapable()` to drive the status-bar
badge.

## Examples

Inspect what's on disk:

```bash
bernstein worktrees list
```

Preview reap plan without touching disk:

```bash
bernstein worktrees gc --dry-run
```

Reap non-interactively (CI / cron):

```bash
bernstein worktrees gc --yes
```

JSON dump for piping into `jq`:

```bash
bernstein worktrees list --json | jq '.[] | select(.state == "orphan")'
```

## Troubleshooting

**Exit code 2 from `gc`.** A peer `gc` holds the lock. Inspect
`.sdd/runtime/worktree-gc.lock`; if its PID is dead, remove the file
and retry. Otherwise wait for the peer to finish.

**`active` row but the task already died.** Either the task record at
`.sdd/runtime/pids/<sid>.json` is stale (PID matches a recycled
process) or the last trace is fresh enough that the classifier holds
`active`. Wait 24h for the rule to flip to `stale`, or remove the
`pids/<sid>.json` file by hand.

**`corrupt` rows after a bad merge.** Reap them. The classifier marks
any worktree without a `.git` anchor as corrupt regardless of task
record, and `gc` removes the directory tree.
