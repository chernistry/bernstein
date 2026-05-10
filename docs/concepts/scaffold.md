# Prompt-to-repo scaffold

`bernstein scaffold "<prompt>"` materialises a small project
skeleton from a single goal prompt. A deterministic keyword
heuristic picks one template family from the registry, the template
is rendered to disk, and the generator returns the list of created
paths.

## Why it exists

Real project starts spend the first hour on the same scaffolding:
`README.md`, a stub entry point, a test directory, a license file,
a one-line CI config. None of that is interesting; all of it is
reproducible from a single sentence describing the goal.

The CLI ships the smallest viable slice of that workflow: pick a
template, render it, write to disk. The full goal-to-deploy flow
(architect, backend, frontend, reviewer; preview tunnel; deploy
adapter) composes on top of this primitive in follow-up slices.

## How to use it

```bash
# Auto-pick a template via keyword heuristic on the prompt
bernstein scaffold "Build me a habit tracker"

# Pin a specific template
bernstein scaffold "CLI to convert markdown to PDF" --template python-cli

# Custom output directory (defaults to ./<slug-of-prompt>)
bernstein scaffold "static landing page" --output ./my-site

# Allow writing into a non-empty directory
bernstein scaffold "habit tracker" --output ./existing --force
```

The output is a populated directory with a working entry point,
a `README.md` that paraphrases the prompt, and the next-step
command printed at the end:

```text
Scaffolded python-cli into /path/to/habit-tracker
  - README.md
  - pyproject.toml
  - src/habit_tracker/__init__.py
  - src/habit_tracker/cli.py
  - tests/test_cli.py

Next: cd /path/to/habit-tracker && cat README.md
```

## Configuration

| Flag | Default | Meaning |
|---|---|---|
| `PROMPT` | required | The free-form prompt; drives `auto` template pick and slug. |
| `--template NAME` | `auto` | One of the registered templates. `auto` runs the keyword heuristic. |
| `--output DIR` | `./<slug-of-prompt>` | Destination directory. |
| `--force` | off | Allow writing into a non-empty directory. |

Run `bernstein scaffold --help` to see the live template list.

## Limitations

- The keyword heuristic is intentional but coarse. When the prompt
  matches several template families weakly, pin one with
  `--template NAME` rather than rely on `auto`.
- Templates are static. Variable substitution is limited to slug
  and prompt; richer parameterisation lives in follow-up slices.
- No git initialisation, no commit, no remote push. The generator
  writes files and returns; downstream tooling decides what to do
  with them.

## Related

- Source: `src/bernstein/cli/commands/scaffold_cmd.py`
- Templates: `src/bernstein/cli/scaffold/templates.py`
