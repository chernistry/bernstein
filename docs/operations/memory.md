# Memory: source-adapter provenance and the cross-adapter read filter

`SQLiteMemoryStore` (`src/bernstein/core/memory/sqlite_store.py`) is the
persistent, tag-indexed store that backs Bernstein's cross-session memory.
Every row records the content, type, tags, importance, and a timestamp; from
this release each row can also record the CLI adapter that produced it.

The provenance field and its matching read filter are both opt-in. Existing
operators see no behaviour change unless they pass the new keywords.

## What changed

| Surface | Old | New |
|---------|-----|-----|
| Schema | `memory(... source_agent, source_model)` | adds `source_adapter TEXT` |
| `SQLiteMemoryStore.add` | unchanged | accepts `source_adapter: str \| None = None` |
| `SQLiteMemoryStore.add_many` | n/a | bulk insert with per-row `source_adapter` |
| `SQLiteMemoryStore.query` | n/a | yields rows; supports `read_only_from_adapters` allow-list |
| `MemoryEntry` | `source_agent`, `source_model` | adds `source_adapter: str \| None` |
| `CrossTaskKB.publish` | unchanged | forwards `source_adapter` to the store |
| `CrossTaskKB.subscribe` | unchanged | accepts `read_only_from_adapters` |

The migration is additive: `_migrate_columns` adds `source_adapter` only if
absent. Existing rows backfill with `NULL` and continue to surface from
`list()`, `query()`, and `get_relevant()` by default.

## Threat model

Bernstein routes tasks across a heterogeneous set of adapters
(claude-code, codex, gemini-cli, ...). A row written by adapter A is, by
default, indistinguishable from a row written by adapter B once it lands in
the shared SQLite store. A subscriber operating under adapter B will replay
adapter A's payload verbatim.

That is the shape of a documented cross-adapter memory-poisoning attack
class: a payload written by one adapter steers a later, unrelated adapter
into following injected instructions or exfiltrating data. The orchestrator
sits at the boundary where this isolation has to live; no single adapter can
enforce it for the others.

Recording the producing adapter and offering an opt-in allow-list on read is
the minimum primitive needed to draw that boundary. Operators or wrappers
that want stricter isolation flip the read filter on; the default is
unchanged so existing flows keep working.

## Usage

### Write with provenance

```python
from pathlib import Path
from bernstein.core.memory.sqlite_store import SQLiteMemoryStore

store = SQLiteMemoryStore(Path(".sdd/memory/memory.db"))

store.add(
    type="learning",
    content="HTTP 429 from the upstream API means back off, not retry-now.",
    tags=["http", "retries"],
    source_adapter="claude-code",
)
```

`add_many` accepts the same per-row keyword set and writes the batch in a
single transaction:

```python
store.add_many(
    [
        {"type": "learning", "content": "...", "source_adapter": "claude-code"},
        {"type": "learning", "content": "...", "source_adapter": "codex"},
    ]
)
```

### Read with an adapter allow-list

```python
# Default: every row, including pre-migration NULL-provenance rows.
for entry in store.query():
    ...

# Strict allow-list: rows whose source_adapter is in the list, no NULLs.
for entry in store.query(read_only_from_adapters=["claude-code"]):
    ...
```

Passing an empty list (`read_only_from_adapters=[]`) is treated as "no
adapter is allowed" and returns nothing. This is intentional: an explicit
empty allow-list is a safe default for callers that want a fail-closed read.

### Forwarding through `CrossTaskKB`

Adapters that already use the publish/subscribe facade pick up the feature
without extra wiring:

```python
from bernstein.core.memory.cross_task_kb import CrossTaskKB

kb = CrossTaskKB(store, run_id="r-1", producer_task_id="t-7")
kb.publish(
    tag="api-schema",
    key="users",
    value="...",
    scope="run",
    source_adapter="claude-code",
)

for fact in kb.subscribe(
    tag="api-schema",
    scope="run",
    read_only_from_adapters=["claude-code"],
):
    ...
```

## Migration notes

- New databases pick up `source_adapter` on first open.
- Existing databases gain the column the next time
  `SQLiteMemoryStore.__init__` runs. The migration is idempotent: re-opening
  the same database does not raise on the duplicate `ADD COLUMN`.
- Pre-migration rows persist with `source_adapter = NULL` and are returned by
  default `list()`, `query()`, and `get_relevant()` calls so existing tooling
  sees no read regression.
- Rows with `NULL` provenance are excluded from `query(read_only_from_adapters=...)`
  results because the filter is a strict allow-list, not a default-deny.

## Out of scope (v1)

- A global per-adapter read isolation policy. This ticket adds the
  primitive; policy wiring is a follow-up.
- Lineage-style content-hash chains across rows. `cross_task_kb_meta`
  already carries `content_hash` per published fact.
- Provenance on the JSONL log under `memory/jsonl_log.py`. Add it
  separately if the SQLite primitive proves out.
