# Retry budget (criterion-aware)

Audience: operators tired of identical retries that re-burn the same
budget and produce the same failure.

## Overview

The default retry path reruns with the same model, prompt, and gate
criteria - so attempt #2 typically fails the same way as attempt #1.

The criterion-aware retry budget instead dials down a *named criterion*
on each retry. The first retry degrades the first criterion, the
second retry the second, and so on. Once every criterion has been
degraded the floor is held: further retries are permitted but produce
no additional degradation.

Source:

- `src/bernstein/core/cost/retry_budget.py`
- `src/bernstein/cli/run_bootstrap.py` (the `--retry-budget` flag)

## Spec syntax

```
N retries, degrade: <criterion1>[>criterion2[>criterion3...]]
```

Examples:

| Spec | Behaviour |
|------|-----------|
| `3 retries, degrade: coverage>tests>style` | Attempt 1: coverage degraded. Attempt 2: tests degraded. Attempt 3: style degraded. |
| `2 retries, degrade: coverage` | Attempt 1: coverage degraded. Attempt 2: no further degradation (held at floor). |
| `0 retries, degrade: coverage` | Never retries; useful as a "fail fast" pin. |

Parser: `parse_retry_budget_spec()`. Errors raised eagerly at CLI
parse time (before any agent spawns):

- `UnknownCriterionError` - unknown criterion name
- `DuplicateCriterionError` - same criterion listed twice
- `RetryBudgetError` - generic malformed spec

## CLI

```bash
bernstein run plan.yaml --retry-budget "3 retries, degrade: coverage>tests>style"
```

The parsed spec is validated eagerly, then exported to the orchestrator
via `BERNSTEIN_RETRY_BUDGET_SPEC`.

## Criterion semantics

Each criterion is a `(name, level, min_level, max_level)` tuple. Each
degradation step decrements the level by `1` until `min_level`
(`is_at_floor` returns `True` at that point). The actual
interpretation of a criterion name (e.g. `coverage`, `tests`, `style`)
is owned by the gating layer that consumes the snapshot.

## Snapshot returned per retry

`RetryDecision` (frozen dataclass) is what callers consume on each
retry decision:

| Field | Use |
|-------|-----|
| `should_retry` | `True` while the budget is not exhausted |
| `degraded_criterion` | The criterion lowered on this retry (or `None` at the floor) |
| `degradation_kind` | One of the documented degradation kinds |
| `criteria_snapshot` | Current `(name -> level)` mapping after the step |
| `reason` | Human-readable rationale for logs / decisions |

## Errors

| Class | Cause |
|-------|-------|
| `RetryBudgetError` | Base class for the sealed hierarchy |
| `CriterionExhaustedError` | A criterion below `min_level` was asked to degrade |
| `DuplicateCriterionError` | Two entries with the same name |
| `UnknownCriterionError` | Spec references a criterion the consumer never registered |
| `RetryBudgetExhaustedError` | All retries already consumed |

## Examples

Tighten the budget for a flaky pipeline:

```bash
bernstein run plan.yaml --retry-budget "2 retries, degrade: coverage>tests"
```

Disable retries entirely for a deterministic CI run:

```bash
bernstein run plan.yaml --retry-budget "0 retries, degrade: coverage"
```

## Troubleshooting

**`invalid --retry-budget value`.** Click rejected the spec eagerly.
The error string names the offending token (unknown criterion,
duplicate name, or malformed `>` chain).

**Retries happen but the criterion never seems to degrade.** Confirm
the consumer reads `BERNSTEIN_RETRY_BUDGET_SPEC` and calls
`RetryBudget.consume()` rather than just `peek()`. The peek path
returns the next decision but does not advance the cursor.
