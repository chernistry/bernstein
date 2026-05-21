# Snapshot undo and stacked branches

Bernstein takes a cheap git snapshot of the work tree before every
tool call that mutates files. Operators can rewind any single step,
diff two snapshots, and inspect the chain of branches each task ran
through.

## TL;DR

| Command | Purpose |
|---|---|
| `bernstein git snapshots [--task <id>]` | List recent snapshots, newest first |
| `bernstein git undo <snapshot_id>` | Restore the work tree to that snapshot |
| `bernstein git diff <a> <b>` | Show diff --stat between two snapshot trees |
| `bernstein git stack --task <id>` | List branches in the task's stack (oldest first) |
| `bernstein git stack-clear --task <id>` | Drop the stack ordering refs for a task |
| `bernstein git gc [--days N]` | Garbage-collect snapshots older than N days (default 30) |

All commands operate on the current working directory by default;
pass `--workdir <path>` to point at another repo.

## How snapshots work

A snapshot is a single git tree object. Capturing one is cheap: the
orchestrator drives a throwaway index (the same trick `git stash`
uses) and writes the tree under the side ref namespace
`refs/bernstein/snapshots/<id>`. Identical trees deduplicate by content
hash, so two snapshots between near-identical states cost a few
hundred bytes each.

Snapshots are never pushed by the default `git push` invocation - the
ref namespace is local-only. If you want to share them, push the refs
explicitly: `git push origin 'refs/bernstein/snapshots/*'`.

### What gets captured

- Every tracked file's current contents.
- Every untracked file in the work tree (`git add -A` against the
  throwaway index).
- File deletions, as removals against the parent tree.

What is **not** captured:

- Environment variables, secrets caches, or anything outside the work
  tree.
- The agent's deliberate staging decisions - the real index is left
  untouched.

## Pre-tool-use hook

The orchestrator calls `SnapshotStore.take(...)` from the
`preToolUse` lifecycle hook for any tool call that mutates the
workspace. Each snapshot records the task ID, tool call ID, and agent
slug so it joins cleanly against the lineage audit log.

Disabling: set `BERNSTEIN_SNAPSHOTS_DISABLED=1` in the agent's
environment, or override the hook in `bernstein.yaml`.

## Stacked branches

Each agent run inside a task creates its own branch. Rather than all
runs targeting the same base, Bernstein **stacks** them: run N+1's
branch is created from the tip of run N's branch. This keeps the
chronological order visible in the eventual PR review.

The stack is recorded under `refs/bernstein/stacks/<task_id>/<n>` -
one ref per run. `bernstein git stack --task <id>` enumerates the
stack in ascending order.

When a task is archived, call `bernstein git stack-clear --task <id>`
to drop the stack ordering refs (snapshots are left intact and follow
the normal GC window).

## Retention

The default retention window is **30 days**. Run
`bernstein git gc --days 30` periodically (or wire it into your
maintenance cron) to prune older snapshots. The command only deletes
the side refs and metadata sidecars; the orphaned tree objects are
reclaimed by the next normal `git gc` pass.

## CLI examples

```
# List the last 20 snapshots for one task.
bernstein git snapshots --task T-1234 --limit 20

# Rewind to a specific snapshot. Refuses to clobber uncommitted
# changes unless you pass --force.
bernstein git undo 20260519T091201Z-9af83b7d4c10

# What changed between two checkpoints?
bernstein git diff 20260519T091201Z-9af83b7d4c10 20260519T091534Z-aabbccddeeff

# Show the branch stack for a task.
bernstein git stack --task T-1234

# Prune anything older than the default window.
bernstein git gc
```

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `not inside a git work tree` | `--workdir` points outside a repo | Pass `--workdir <repo-root>` or `cd` into it |
| `work tree has uncommitted changes` | Dirty undo guardrail | Commit / stash, or pass `--force` |
| `snapshot <id> not found` | Snapshot was GC'd or never existed | List snapshots first; check the retention window |
| `branch <name> does not exist` | `stack_push` was called before the branch | Create the branch first, then record the stack entry |

## API reference

Module: `bernstein.core.git.snapshot`

- `SnapshotStore(cwd)` - facade with `take`, `undo`, `list`, `diff`,
  `get`, `delete`, `gc`.
- `Snapshot` - frozen dataclass of captured metadata.
- `stack_push(cwd, *, task_id, branch, parent_branch=None)` - record
  a stack entry.
- `stack_list(cwd, *, task_id)` - enumerate stack entries.
- `stack_clear(cwd, *, task_id)` - drop every stack entry for a task.
