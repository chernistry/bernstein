# Fresh-context review gate

The review gate is a typed pipeline stage that runs **after** an
implementer agent completes a PR. It hard-asserts that the reviewer

1. runs in a **fresh session** (new session id; the implementer's
   transcript is never threaded in),
2. runs against a **distinct model** per the configured selection rule,
3. sees **only** `(spec, diff, test_output)` as inputs, and
4. returns a **three-valued structured verdict** -- `pass`, `fail`, or
   `questions` -- that drives the auto-merge decision.

## Why

Same-model self-critique is empirically weak at catching the
implementer's own blind spots, and long-running implementer contexts
accumulate drift that makes the implementer a poor reviewer of its own
diff. Making the rules a property of configuration -- rather than
convention or habit -- eliminates the silent "we reused the session by
accident" failure mode.

## Module

`bernstein.core.quality.review_pipeline.review_gate`

Public symbols (also re-exported from
`bernstein.core.quality.review_pipeline`):

| Symbol | Purpose |
|---|---|
| `ReviewGate` | Frozen dataclass that runs the gate |
| `ModelSelection` | `SameModelOk` / `DifferentModelPreferred` / `DifferentModelRequired` |
| `ImplementerContext` | Carries `model`, `session_id`, optional `transcript` |
| `ReviewInputs` | `(spec, diff, test_output)` -- only inputs the reviewer sees |
| `ReviewVerdict` | Structured verdict: `state`, `summary`, `issues`, `questions`, `confidence` |
| `parse_structured_verdict` | Default JSON parser; unknown / unparseable states map to `fail` |
| `EvalGateConfigError` | Raised when `DifferentModelRequired` cannot be satisfied |
| `FreshContextViolation` | Raised when implementer transcript leaks into the reviewer prompt |

## Contract

| Property | Behaviour |
|---|---|
| `requires_fresh_session` | Always `True`; constructing with `False` raises `FreshContextViolation` |
| Session id | Generated via `secrets.token_urlsafe`; always distinct from the implementer's |
| Prompt assembly | Built from `(spec, diff, test_output)` only |
| Transcript leak guard | Post-condition check raises `FreshContextViolation` if implementer transcript text appears verbatim in the reviewer prompt |
| Verdict states | `pass` (only state that permits auto-merge), `fail`, `questions` |
| `blocks_merge()` | Returns `True` for `fail` and `questions`; `False` for `pass` only |

## Model-selection rule

| Mode | Behaviour |
|---|---|
| `SameModelOk` | Reviewer may use the same model as the implementer |
| `DifferentModelPreferred` | Pick a distinct model when one is configured; otherwise fall back to the implementer model and log a warning |
| `DifferentModelRequired` | Pick a distinct model or raise `EvalGateConfigError` |

Model identifiers are compared with provider prefixes stripped, so
`anthropic/claude-foo` and `claude-foo` are treated as the same model
when checking the distinct-model rule.

## Verdict schema

Reviewer responses are parsed by `parse_structured_verdict` from JSON of
the form:

```json
{
  "state": "pass" | "fail" | "questions",
  "summary": "one or two sentences",
  "issues": ["specific issues that drove a fail verdict"],
  "questions": ["open questions when state == questions"],
  "confidence": 0.0
}
```

Defensive defaults:

- Unknown `state` values map to `fail` (blocks merge).
- Unparseable responses map to `fail` (a reviewer outage never silently
  green-lights a merge).
- `confidence` is clamped to `[0.0, 1.0]`.

## Auto-merge integration

The orchestrator must observe `ReviewVerdict.blocks_merge() == False`
before triggering auto-merge:

```python
verdict = await gate.run(implementer_ctx, review_inputs, candidates=models)
if verdict.blocks_merge():
    # surface `verdict.issues` / `verdict.questions` to the implementer
    return
# proceed to auto-merge
```

## Example

```python
from bernstein.core.quality.review_pipeline import (
    ImplementerContext,
    ModelSelection,
    ReviewGate,
    ReviewInputs,
    parse_structured_verdict,
)

gate = ReviewGate(
    reviewer_call=my_async_reviewer,
    verdict_parser=parse_structured_verdict,
    model_selection=ModelSelection.DifferentModelRequired,
)

implementer = ImplementerContext(
    model="anthropic/claude-impl",
    session_id="impl-session-xyz",
)
inputs = ReviewInputs(spec=spec_text, diff=diff_text, test_output=test_log)

verdict = await gate.run(
    implementer,
    inputs,
    candidates=["openai/gpt-review", "google/gemini-review"],
)
```

## Tests

Unit tests live at `tests/unit/review_gate/test_review_gate.py` and cover:

- Fresh-context separation (session id distinct; transcript never
  reaches the prompt; smuggled transcript trips
  `FreshContextViolation`).
- Structured-verdict schema (round-trip; defensive defaults; clamping;
  code-fence stripping).
- Fail-blocks-merge path.
- Questions-block-merge path.
- Model-selection rule under each `ModelSelection` mode, including
  explicit override and `EvalGateConfigError` paths.
