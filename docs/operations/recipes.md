# Recipes (first-class workflow library)

Audience: operators who want a parameterised workflow they can invoke
in one line instead of authoring a `WorkflowSpec` from scratch.

## Overview

A recipe is a parameterised workflow manifest. Each manifest lives at
`templates/recipes/<name>.yaml` and reuses
`bernstein.core.workflows.workflow_spec.WorkflowSpec` for the node
body. A top-level `params:` block adds operator-facing typed inputs.

The CLI validates parameters, applies defaults, and renders
placeholders before handing the resolved `WorkflowSpec` to the existing
`WorkflowRunner`.

Source:

- `src/bernstein/cli/commands/recipes_cmd.py`
- `bernstein.core.workflows.recipe_spec.RecipeSpec`
- `templates/recipes/*.yaml`

## Bundled recipes

| Name | What it does |
|------|--------------|
| `refactor-glob` | Rename an identifier pattern across a path, then run tests. |
| `bump-dependency` | Upgrade a Python dep to a target version, run tests, fix breakage, re-run. |
| `add-tests-for-module` | Survey a module's public surface and backfill pytest coverage. |
| `license-audit` | Scan deps for licenses incompatible with the project license; write report. |
| `regenerate-docs` | Refresh module map + API docs, then build and lint the docs site. |

## CLI

```text
bernstein recipes list                                       [--bundled-only]
bernstein recipes show NAME
bernstein recipes run NAME --param key=value [--param ...]   [--dry-run] [-g GOAL]
```

- `list` enumerates bundled + user-installed recipes with their
  one-line descriptions.
- `show` prints the manifest details: params (with types, defaults,
  required flags, choices), nodes, dependency order.
- `run` executes end-to-end. `--dry-run` prints the resolved workflow
  plan without spawning agents.

## Manifest schema

Each manifest declares:

| Section | Use |
|---------|-----|
| `name`, `description`, `version` | Recipe identity |
| `params:` | Typed inputs (`string`, `int`, `float`, `bool`) with `required`, `default`, `choices`, `help` |
| `nodes:` | Standard `WorkflowSpec` nodes; placeholders reference params as `{{ name }}` |

Bad input -> exit `1` with an operator-readable error. Bad manifest ->
exit `2`.

## Examples

Run a glob-rename then test:

```bash
bernstein recipes run refactor-glob \
  --param pattern=foo_ \
  --param replacement=bar_ \
  --param path=src/bernstein \
  --param test_command="pytest -x tests/unit"
```

Dry-run a dependency bump:

```bash
bernstein recipes run bump-dependency \
  --param package=httpx \
  --param version=0.27.0 \
  --dry-run
```

Show the parameter shape of a recipe:

```bash
bernstein recipes show bump-dependency
```

List only bundled recipes (skip user-installed):

```bash
bernstein recipes list --bundled-only
```

## Authoring your own

Drop a YAML file under `templates/recipes/` (or any user-recipe path
the CLI scans). Required header: `name`, `description`, `version`,
`params`, and the standard `WorkflowSpec` body. Re-run
`bernstein recipes list` to confirm pickup.

## Troubleshooting

**`bad param: <name>`.** The value did not match the declared type or
the `choices` whitelist. Re-check `bernstein recipes show <name>` for
the canonical shape.

**`recipe not found`.** Either the file is outside the scanned
directories or the YAML failed to parse. `bernstein recipes list`
prints the resolved paths it scanned.

**Run "succeeds" but no diff appears.** You probably ran with
`--dry-run`. Drop the flag to actually spawn agents.
