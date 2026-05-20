# Layered skill customisation

Bernstein resolves every skill from up to three optional layers. A
higher-precedence layer can override a lower one on a per-field basis,
without forking the in-package skill and without losing the ability to
keep a personal note out of git.

## Layers

| Layer | Path                                          | Intent                                       | Tracked in git |
|-------|-----------------------------------------------|----------------------------------------------|----------------|
| base  | `~/.local/share/bernstein/skills/base/`       | In-package skill shipped with Bernstein.     | n/a            |
| team  | `~/.config/bernstein/skills/team/`            | Project-shared override.                     | usually yes    |
| user  | `~/.config/bernstein/skills/user/`            | Personal override.                           | no             |

Precedence runs `user > team > base`. Missing layers fall through: if
only the team layer is on disk, the team values are returned as-is.

The XDG environment variables `XDG_DATA_HOME` and `XDG_CONFIG_HOME` are
honoured, so operators with non-standard layouts (or CI sandboxes) can
redirect every layer cleanly.

## On-disk forms

Each layer can store a skill in any of these forms:

- `<layer>/<name>.yaml` or `<name>.yml` - flat YAML mapping.
- `<layer>/<name>/SKILL.md` - frontmatter + body (the in-package shape).
- `<layer>/<name>.toml` - flat fragment (treated as YAML-compatible).

The loader picks the first form that exists per layer in the order
above.

## Merge rules

Every top-level field has a documented strategy in `MergeSpec`. Unknown
fields raise `UnknownFieldError` rather than silently no-opping, so a
typo in an override is caught immediately.

| Strategy        | When it applies                                                          | Behaviour                                                                            |
|-----------------|--------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `OVERRIDE`      | Scalars (`name`, `description`, `version`, `author`, `body`).            | Higher layer replaces the lower layer's value wholesale.                             |
| `APPEND`        | Unkeyed arrays (`trigger_keywords`).                                     | Higher layer's items are concatenated after lower layer's items in encounter order.  |
| `KEYED_REPLACE` | Arrays of mappings keyed by `name` / `id` / `code` (`references`, etc.). | Higher layer replaces lower-layer entries with the same key; new keys are appended.  |
| `DEEP_MERGE`    | Nested mappings (`metadata`).                                            | Recurse: identical paths use OVERRIDE semantics, distinct paths coexist.             |

## CLI

```
bernstein skills list --layered             # show base/team/user origin
bernstein skills show <name> --per-layer    # merged + per-layer diff
```

`--per-layer` prints the merged skill as deterministic JSON
(`sort_keys=True`), then each contributing layer as raw JSON so an
operator can debug exactly which value won.

## Determinism

The merge function is pure. Given identical input fragments, it
produces identical output every time:

- `merge_layers({...})` is idempotent (re-running on the same input
  gives the same result).
- `Skill.as_dict()` returns a JSON-stable shape (no tuples, sorted
  keys when serialised).

This matters for review: a layered skill committed to git produces
byte-identical effective skill manifests across machines.

## Examples

### Per-field override

`base/writer.yaml`:

```yaml
description: base description
author: core
trigger_keywords: [base-kw]
```

`user/writer.yaml`:

```yaml
author: me
```

Effective skill: description from `base`, author from `user`, keywords
unchanged.

### Keyed replace

`base/writer.yaml`:

```yaml
references:
  - name: style
    url: base-style
```

`user/writer.yaml`:

```yaml
references:
  - name: style
    url: user-style
  - name: extras
    url: user-extras
```

Effective `references`: `style` is replaced (URL from user), `extras`
appended.

### Deep merge

`base/writer.yaml`:

```yaml
metadata:
  limits:
    max_tokens: 1000
    temperature: 0.2
```

`user/writer.yaml`:

```yaml
metadata:
  limits:
    max_tokens: 2000
```

Effective `metadata.limits`: `{"max_tokens": 2000, "temperature": 0.2}`.

## Acceptance tests

`tests/unit/skills/test_layered.py` pins:

- Layer precedence for each strategy.
- Per-field override granularity.
- Deterministic merge across runs.
- Missing-layer fall-through (base-only, user-only, team+user).
- Unknown-field rejection.
- Filesystem loading for YAML and SKILL.md forms.
