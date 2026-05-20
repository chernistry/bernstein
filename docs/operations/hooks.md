# Hook permission-rule prefilter

This page documents the optional `if:` filter on config-declared hooks.
For the full hook contract (event vocabulary, payload schemas, the
allow/deny/mutate/annotate stdout protocol, and discovery) see
[the hook contract reference](../contributing/hooks.md).

## TL;DR

| Item | Value |
|------|-------|
| Where | `bernstein.yaml`, under a `script:` entry's `if:` key |
| Purpose | Skip the hook subprocess spawn when the event does not match |
| Grammar | `Bash(git *)`, `Read(/path/*)`, `Tool(name)`, or a bare `Bash` |
| Default | No `if:` means the hook always runs (legacy behaviour) |
| On non-match | The spawn is skipped and a `hook.filtered` metric is emitted |
| On bad filter | A parse error is raised at config load, before the hook registers |

## Why

A config-declared hook subscribes to one event type and is spawned for
every occurrence of that event. The hook script then parses its input and
decides whether it applies. For hooks that only care about a narrow slice
of events, the subprocess cold-start cost dominates.

The `if:` filter lets the lifecycle runner evaluate a declarative rule
against the event payload first, and short-circuit before paying the spawn
cost on irrelevant events. The grammar is the same one used by the
permission engine, so the filter language is already familiar.

## Grammar

A filter is a single tool selector with an optional argument glob:

| Form | Matches |
|------|---------|
| `Bash(git *)` | a `Bash` tool whose `command` glob-matches `git *` |
| `Read(/etc/**)` | a `Read` tool whose `path` glob-matches `/etc/**` |
| `Tool(grep)` | any tool whose name glob-matches `grep`, with no argument constraint |
| `Bash` | any `Bash` invocation, regardless of arguments |

Notes:

- Tool names match case-insensitively.
- The argument is treated as a `command` glob for shell-like tools
  (`Bash`, `shell`, `sh`, `exec`, `run`) and as a `path` glob otherwise.
- Globs support `*`, `?`, `[seq]`, and `**` for deep path matching, exactly
  as the permission engine does.
- A filter only matches event payloads that carry a `tool` key (the
  tool-scoped events). Events without a tool never match a tool-scoped
  filter and are skipped.

## Where the filter reads from

The filter is evaluated against the event payload's `data` mapping. For
tool-scoped events that mapping carries:

- `tool` -- the tool name.
- `args` -- the tool input mapping (for example `{"command": "git push"}`
  or `{"path": "/etc/passwd"}`).

## Examples (one per event family)

```yaml
hooks:
  # Cross-CLI tool events: filter on the tool and its arguments.
  preToolUse:
    - script: "scripts/guard-force-push.sh"
      if: "Bash(git push *--force*)"
  postToolUse:
    - script: "scripts/audit-writes.sh"
      if: "Write(/etc/**)"

  # A name-only selector: run for one tool regardless of arguments.
  errorOccurred:
    - script: "scripts/page-on-grep-error.sh"
      if: "Tool(grep)"

  # Bernstein-native lifecycle events carry no tool payload, so a
  # tool-scoped filter would skip every invocation. Omit `if:` for these
  # so the hook always runs.
  post_merge:
    - script: "scripts/notify.sh"
      timeout: 10
```

## Failure modes

- A malformed filter (`Bash(`, `Tool()`, empty string, trailing tokens)
  raises a config error at load time. The hook does not register and the
  operator gets a clear message naming the bad expression.
- A non-matching filter is not an error: the spawn is skipped and a
  `hook.filtered` metric is emitted with the unmatched filter as its
  reason. Use this metric to confirm a filter is doing what you expect.
