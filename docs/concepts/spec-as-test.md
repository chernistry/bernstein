# Spec-as-test loop

Plan files describe acceptance criteria as free-text. The
`spec_assertions` module turns those bullets into **executable
assertions** — file-exists / import-resolves / regex-in-file /
test-passes — runs them after each stage drain, and routes failures
back as auto-fix tasks or human-review bulletins.

## Why it exists

Before this loop, "spec said X, code does X" was a human re-read of
the plan. Silent drift between plan intent and merged code went
unnoticed until a human noticed. This pattern complements the
[feature contract](feature-contract.md) — that one freezes the WHAT,
spec-as-test verifies the IS.

## How to use it

Write your plan with explicit `acceptance:` bullets:

```yaml
stages:
  - name: feature
    steps:
      - role: backend
        goal: "Add /healthz endpoint"
        acceptance:
          - "file_exists: src/api/healthz.py"
          - "import_resolves: api.healthz.healthz_view"
          - "regex_in_file: src/api/__init__.py /from .healthz import/"
          - "test_passes: pytest tests/api/test_healthz.py"
```

`extract_assertions(plan)` parses the bullets into typed `Assertion`
records. `run_assertions(assertions, repo_root)` executes them after
every stage drain. Failures post a bulletin and create an auto-fix
task targeting the offending step.

You can emit the assertions as a real pytest file for CI:

```python
from bernstein.core.planning.spec_assertions import (
    extract_assertions, assertions_to_pytest,
)

assertions = extract_assertions(plan)
assertions_to_pytest(assertions, out_path="tests/spec/test_plan_jwt.py")
```

Disable the loop for a run with `--no-spec-test` (default-on).

## Supported assertion kinds

| Kind | Predicate |
|---|---|
| `file_exists` | path resolves to a regular file |
| `import_resolves` | dotted import succeeds in the project venv |
| `test_passes` | named pytest selector exits 0 |
| `regex_in_file` | regex matches at least once in the file's bytes |

Unknown kinds parse as `unknown` and are skipped with a logged
warning.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `spec_assertions.enabled` | `true` | Master switch (CLI: `--no-spec-test`). |
| `spec_assertions.run_on_drain` | `true` | Run after every stage drain. |
| `spec_assertions.emit_pytest` | `false` | Also write pytest files to `tests/spec/`. |

## Limitations

- Only the four kinds listed above. Property-based testing,
  Gherkin/BDD, and LLM-generated assertions are out of scope.
- Assertions are derived from the existing `acceptance:` field; no
  separate spec format.
- The pytest emitter writes synchronous tests; async-only test suites
  need a custom runner wrap.
- Failures attach an auto-fix task but never block merge by themselves
  — the existing janitor + quality gates remain the merge gate.

## Related

- Source: `src/bernstein/core/planning/spec_assertions.py`
- Drain hook: `src/bernstein/core/orchestration/drain.py`
- Run flag: `src/bernstein/cli/commands/run_cmd.py`
- PR #1003, ticket `2026-04-30-feat-spec-as-test-loop.md`
