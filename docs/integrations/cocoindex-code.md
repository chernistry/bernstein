# cocoindex-code (MCP catalog)

[CocoIndex](https://cocoindex.io/cocoindex-code) ships an MCP server,
**cocoindex-code**, that gives spawned agents AST-aware semantic code
search across the whole repository. One MCP call replaces the dozens of
`grep` and `find` invocations a freshly-spawned agent would otherwise
fan out, which cuts token usage on codebase-context tasks.

Bernstein registers cocoindex-code as a *first-class catalog entry* but
it is **available, disabled by default**: the entry ships in the wheel
and shows up in `bernstein mcp catalog list`, but no agent actually
talks to it until an operator opts in.

- Upstream: <https://github.com/cocoindex-io/cocoindex> (Apache-2.0)
- Product: <https://cocoindex.io/cocoindex-code>
- Manifest: `src/bernstein/core/protocols/mcp_catalog/manifests/cocoindex_code.yaml`
- Config flag: `mcp.catalog.cocoindex_code.enabled` (default `false`)

## When to enable it

Turn it on for projects where agents repeatedly search the codebase by
keyword, symbol, or near-miss neighbour:

- Large monorepos (>100 KLOC) where `grep` returns dozens of unrelated hits.
- Heavy refactor work that crosses many files and asks "where else is
  this pattern used?".
- Long-running self-evolving runs whose token bills are dominated by
  full-file reads on near-miss matches.

It is overkill — and wasted disk — for short-lived single-feature
projects, leaf libraries, or runs that touch a known small set of files.

## Resource cost

cocoindex-code maintains a local on-disk index per repository. Expect:

- Disk: ~5-50 MB per 100 KLOC of indexed source. Larger for repos with
  many vendored dependencies; tune by adjusting cocoindex's `.coco/`
  ignore list.
- Memory: ~200-400 MB resident while indexing; ~50-100 MB while serving
  queries.
- CPU: brief CPU spike on every commit while the incremental indexer
  catches up. Idle the rest of the time.
- Network: none after install. The default
  SentenceTransformer embedding model runs locally; no API key required.

## Privacy and security

cocoindex-code embeds the contents of your source tree to build the
semantic index. Treat the index as source-equivalent:

- The index lives outside `.sdd/` (kept outside the snapshot tree on
  purpose so it doesn't bloat run archives) — by default cocoindex
  writes under the user cache directory chosen by the upstream
  defaults. Confirm the path in your environment before enabling on
  shared machines.
- No telemetry is sent in the default configuration. If you swap to a
  cloud embedding provider via LiteLLM, the embedding API receives
  source contents — review your data-handling policy first and gate
  the upgrade through `bernstein.core.security.policy_engine`.
- The bundled manifest pins a specific cocoindex version
  (see `version_pin` in the YAML). Upgrades go through the same
  sandboxed dry-run preview every other catalog entry uses.

## Enable it

```bash
# 1. Confirm the entry is bundled and currently disabled.
bernstein mcp catalog list

# 2. Flip the flag in bernstein.yaml under the tuning section.
#    tuning:
#      catalog:
#        cocoindex_code_enabled: true

# 3. Run the sandboxed dry-run install preview, then register on confirm.
bernstein mcp catalog install cocoindex-code
```

## Disable it

```bash
bernstein mcp catalog uninstall cocoindex-code
# and unset tuning.catalog.cocoindex_code_enabled (or set it back to false).
```

The local manifest is left in place so the entry continues to surface
in `bernstein mcp catalog list` for future re-enable.
