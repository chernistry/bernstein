# Wiki build

`bernstein wiki build` renders a deterministic Markdown wiki for the
current repository directly from the AST symbol graph and the
`agents.md` canonical IR. Output streams to stdout by default or
writes to `WIKI.md` at the repo root with `--write`. The render is
pure, deterministic, and runs locally with no network round-trip.

## Why it exists

Repo wikis exist as a hosted SaaS with per-seat pricing in several
products. The same surface area is a derived view over code that
already lives on disk: the symbol graph, the file list, and the
`agents.md` IR. There is no reason a project cannot render the wiki
on its own laptop, on every commit, for free.

The build is also useful as a CI artefact. Because the output is
deterministic for a given repo state, a workflow can render
`WIKI.md`, diff against the committed copy, and fail if they drift.

## How to use it

```bash
# Stream the rendered wiki to stdout
bernstein wiki build

# Write to WIKI.md at the repo root
bernstein wiki build --write

# Custom output path (implies --write); useful for CI snapshots
bernstein wiki build --output ./build/repo-wiki.md

# Render a wiki for a different repo without leaving the current shell
bernstein wiki build --repo /path/to/other/repo --write
```

The renderer reads:

- `git ls-files` for the visible source set.
- The AST symbol graph from `core.knowledge.ast_symbol_graph`.
- The optional `agents.md` IR if one is present at the repo root.

It produces a Markdown table of contents, a per-package symbol
listing, and links into the source tree. No filesystem mutation
happens unless `--write` or `--output` is set.

## Configuration

| Flag | Default | Meaning |
|---|--:|---|
| `--repo PATH` | current working directory | Repo root to scan. |
| `--write` | off | Write output to `WIKI.md` at the repo root. |
| `--output PATH` | unset | Custom output path; implies `--write`. |

## Limitations

- This is the smallest viable slice of the wiki feature. HTTP
  routes, MCP server exposure, and post-commit re-indexing are
  not yet wired in.
- The renderer is read-only. It does not touch git history or push
  anywhere.
- The symbol graph is derived from Python AST. Other languages
  appear in the wiki as bare file entries with no symbol detail.

## Related

- Source: `src/bernstein/cli/commands/wiki_cmd.py`
- Symbol graph: `src/bernstein/core/knowledge/ast_symbol_graph.py`
- Renderer: `src/bernstein/core/knowledge/wiki_renderer.py`
- [Cross-CLI agent-context sync (agents-md)](../agents-md.md)
