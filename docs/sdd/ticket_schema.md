# SDD ticket schema (v1)

Bernstein ships a portable JSON Schema for the YAML frontmatter on every
`.sdd/backlog/*.md` (or `.yaml`) ticket. The same schema is reachable from
any project that depends on Bernstein, via `importlib.resources` or via the
`bernstein ticket validate` CLI.

## TL;DR

| What | Where |
|------|-------|
| Schema file | `bernstein/sdd/schema/ticket.v1.json` (Draft-07) |
| Loader API | `bernstein.sdd.validator.load_schema("v1")` |
| Validator API | `bernstein.sdd.validator.validate_ticket(path)` |
| CLI | `bernstein ticket validate <path-or-glob>` |
| Exit codes | `0` pass / `1` fail / `2` schema not found |

## Required keys

| Key | Type | Notes |
|-----|------|-------|
| `id` | string | Slug, `^[a-z0-9][a-z0-9-]{2,80}$` |
| `created` | string | ISO 8601 date, `YYYY-MM-DD` |
| `status` | enum | `open`, `claimed`, `in_progress`, `blocked`, `closed`, `closed_hit`, `closed_miss`, `closed_partial`, `deduped`, `superseded` |
| `priority` | enum | `P0`, `P1`, `P2` |
| `effort` | enum | `S`, `M`, `L` |

A ticket missing any of these fields is a hard failure.

## Recommended keys

Missing keys produce **warnings** (or **errors** under `--strict`).

| Key | Type | Notes |
|-----|------|-------|
| `owner` | string \| null | Single assignee or `null` |
| `success_metric` | object | `name`, `current`, `target`, `window_days >= 1` |
| `acceptance_criteria` | array of strings | At least one item |
| `evidence` | array of objects | Each item needs `source` |
| `risk` | string | Free text |
| `rice` | object | `reach`, `impact`, `confidence (0..1)`, `effort_days (>= 0.25)`, `score` |
| `ladder_to` | string | Strategic anchor |

Extra unknown keys are allowed and ignored.

## CLI usage

```
bernstein ticket validate .sdd/backlog/open/feat-x.md
bernstein ticket validate '.sdd/backlog/open/*.md' '.sdd/backlog/closed/*.yaml'
bernstein ticket validate --strict '.sdd/backlog/open/*.md'
bernstein ticket validate --schema v1 --format json '.sdd/backlog/open/*.md'
```

Default output (human):

```
[OK]   .sdd/backlog/open/feat-foo.md
[FAIL] .sdd/backlog/open/bug-bar.md
        - priority: 'wip' is not one of ['P0', 'P1', 'P2']
[WARN] .sdd/backlog/open/chore-baz.md
        - warning: recommended key missing: success_metric
```

`--format json` emits a single JSON object with `schema`, `strict`,
`reports[]`, and a `summary` block - safe to feed to downstream linters.

## Programmatic usage

```python
from pathlib import Path
from bernstein.sdd import validate_ticket

report = validate_ticket(Path(".sdd/backlog/open/feat-x.md"), strict=False)
if not report.ok:
    for issue in report.errors:
        print(issue.render())
```

## Adoption (downstream project)

1. Add `bernstein` to your dependencies.
2. Run `bernstein ticket validate '.sdd/backlog/open/*.md'` in CI.
3. Optionally wire the schema directly into editor tooling - the file lives
   inside the installed package, reachable via
   `importlib.resources.files("bernstein.sdd.schema").joinpath("ticket.v1.json")`.

## Extending the schema

- New keys: add them as `additionalProperties: true`-compatible entries -
  they ship as recommended unless explicitly required.
- New status values: extend the `status` enum in a v2 schema; never widen v1.
- Promote a warning to a hard requirement: add it to `required` in v2 and
  ship the new file as `ticket.v2.json`. Old tickets keep validating against
  v1 by passing `--schema v1`.

## Testing

```
uv run pytest tests/unit/sdd/ -x -q
uv run pytest tests/property/test_ticket_validator_properties.py -x -q
uv run pytest tests/integration/test_ticket_validate_cli.py -x -q
```
