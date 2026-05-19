# Lineage conflict resolution

Bernstein's per-artefact lineage log captures every write a CLI coding
agent makes. When two-or-more agents working in parallel worktrees write
distinct content for the same artefact off the same parent hash, the
lineage detector marks that pair as a *fork*. The merge primitives in
`src/bernstein/core/lineage/merge.py` already know how to resolve a
fork - the surface documented here is the operator CLI that enumerates
unresolved forks and applies a chosen policy.

## When forks happen

Parallel worktrees touching the same file are the typical source.
Examples:

- Two CLI agents claim the same backlog task and each edit `src/foo.py`
  with different changes before either records a parent of the other.
- A long-running session resumes after a checkpoint and re-writes a
  file an unrelated session also touched while it was idle.
- A steward merge entry is missing or was rolled back, leaving open
  siblings the CI gate refuses.

The lineage gate (`bernstein lineage gate`) flags any artefact with
unresolved forks as a FAIL. The resolution CLI exists so operators do
not have to read exception payloads to understand what to fix.

## Listing unresolved forks

```
bernstein lineage conflicts                    # all artefacts
bernstein lineage conflicts --artefact src/foo.py
bernstein lineage conflicts --json             # machine-readable
```

The default human format prints a side-by-side table per fork:

- artefact path
- competing candidate entry hashes
- sibling agent ids
- timestamp (`ts_ns`) of each candidate
- coarse char-count diff between the canonical bytes of the siblings

`--json` emits the same data as a list of objects suitable for
piping into other tools.

## Picking a policy

Three policies ship with the merge primitives:

| Policy           | Behaviour                                                                 |
|------------------|---------------------------------------------------------------------------|
| `human`          | Interactive prompt. Default. Operator picks a candidate from the listing. |
| `first-writer`   | Earliest `ts_ns` wins; lexicographic `agent_id` tiebreak.                 |
| `agent:<id>`     | The named agent's tip wins; latest write from that agent if it has many.  |

Rules of thumb:

- Use `human` for any irreversible change (production migrations, signed
  artefacts) where you want to read the diff yourself.
- Use `first-writer` for low-stakes parallel work where the second
  writer was a clear duplicate.
- Use `agent:<id>` when a specific agent is the designated owner of a
  file (for example, a reviewer agent overriding a worker).

## Resolving one fork

```
bernstein lineage resolve src/foo.py --policy human
bernstein lineage resolve src/foo.py --policy first-writer
bernstein lineage resolve src/foo.py --policy agent:reviewer --reason "agreed on standup"
```

For `human` policy, add `--diff` to see a unified diff of the two
canonical entries before the prompt, or `--yes` to take the first
candidate without prompting (useful in scripts).

Every successful resolution:

1. Appends a JSONL record to `.sdd/lineage/merge-audit.jsonl` with the
   `lineage.merge_entry` event name, policy, winner hash, parent hash,
   candidate hashes, and any `--reason` text.
2. Forwards the same payload through the lifecycle hook emitter when
   one is registered, so plugins (dashboard, external auditors) see the
   decision in real time.

`bernstein lineage gate` will continue to FAIL until a steward writes
the corresponding merge entry to the log. The CLI surface here is the
operator decision surface; the steward signing pass is unchanged.

## Worked example

Two agents (`agent:a`, `agent:b`) wrote distinct content for
`src/foo.py` against the same parent:

```
$ bernstein lineage conflicts --artefact src/foo.py
1 unresolved fork(s):
                                src/foo.py
+----------+---------+--------+------------------------------+------------------------------+
| Candidate | Agent   | ts_ns  | Content hash                 | Entry hash                   |
+----------+---------+--------+------------------------------+------------------------------+
| candidate | agent:a | 17156… | sha256:0a1f2b…...            | sha256:11de4a…...            |
| candidate | agent:b | 17157… | sha256:9b6e34…...            | sha256:2cf78b…...            |
+----------+---------+--------+------------------------------+------------------------------+
  parent=sha256:7a4d2e...   char-count diff: 312 byte(s)
```

The operator reads the diff and picks `agent:a`:

```
$ bernstein lineage resolve src/foo.py --policy human --diff --reason "agent:a included tests"
[unified diff of canonical entries...]
Resolve fork for src/foo.py
  [1] agent=agent:a ts_ns=171560… entry=sha256:11de4a... content=sha256:0a1f2b...
  [2] agent=agent:b ts_ns=171570… entry=sha256:2cf78b... content=sha256:9b6e34...
Pick a candidate index: 1
Resolved src/foo.py: policy=human winner=sha256:11de4a... agent=agent:a
  reason: agent:a included tests
```

The `lineage.merge_entry` audit record is now in
`.sdd/lineage/merge-audit.jsonl`:

```
{"event":"lineage.merge_entry","timestamp":...,"artefact_path":"src/foo.py",
 "policy":"human","winner_hash":"sha256:11de4a...","candidate_hashes":[...],
 "parent_hash":"sha256:7a4d2e...","reason":"agent:a included tests"}
```

`bernstein status` will also fold the unresolved-fork count into the
lineage row so the next operator does not have to run `conflicts`
first to notice the open forks.

## Related surfaces

- `bernstein lineage forks` - minimal one-line-per-fork listing used by
  the CI gate.
- `bernstein lineage merge` - low-level operator helper that names a
  winning content hash without applying a policy.
- `bernstein lineage gate` - chain-verification gate that fails the
  build until a steward merge entry resolves each fork.
- `docs/compliance/lineage-export.md` - regulator-shaped export of the
  per-artefact lineage chain (includes merge entries once they are
  recorded).
