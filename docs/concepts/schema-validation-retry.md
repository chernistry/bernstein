# Schema validation retry

When a spawned agent emits malformed JSON (manager planning output,
MCP tool response, planner decoder), Bernstein retries up to N times
and **feeds the specific validation error back into the next prompt**.
Errors accumulate across steps so the agent learns "you keep
mis-typing field `priority`."

## Why it exists

Before this loop, schema failures fell into one of two paths: hard
fail (mark the task failed) or generic retry that re-spawned without
the validation error in context. Neither helped the agent self-correct.
The Self-Refine pattern (ICLR 2024) and adjacent work documents 15-45 %
quality improvement from a multi-attempt retry with detailed feedback.

## How to use it

The retry helper is wired into the call sites that decode structured
agent output. From your own code:

```python
from bernstein.core.tasks.schema_retry import (
    SchemaRetryContext,
    validate_with_retry,
)
from pydantic import BaseModel

class PlannerOutput(BaseModel):
    tasks: list[str]
    rationale: str

ctx = SchemaRetryContext()

def ask_again(prompt_with_errors: str) -> str:
    # call your spawned agent / model with the augmented prompt
    return run_agent(prompt_with_errors)

result = validate_with_retry(
    payload=raw_text,
    schema=PlannerOutput,
    ctx=ctx,
    max_attempts=3,
    ask_again=ask_again,
)
```

On a malformed payload the helper:

1. Runs the schema validator (Pydantic / JSONSchema).
2. Records the error into `ctx`.
3. Calls `ask_again(prompt + "you previously got these errors: …")`.
4. Loops up to `max_attempts`; raises `SchemaRetryExhausted` with the
   full error trail on terminal failure.

Errors accumulate across steps in the same `SchemaRetryContext`, so
the same agent across a multi-step pipeline sees the trail of every
prior validation failure, not just the most recent.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `defaults.SCHEMA_RETRY_MAX_ATTEMPTS` | `3` | Per-decode retry budget. |
| `schema_retry_attempts_total{outcome}` | metric | `success` / `retry` / `exhausted` outcomes; scrape for cost-tracking the retry loop. |

## Limitations

- This is a **wrapper** around existing validation, not a replacement
  for Pydantic. Models still own field constraints.
- The agent itself is the repair loop — there is no LLM-based schema
  repair sitting between the validator and the agent.
- Token cost of retries is captured by the existing `cost_tracker`,
  not by this module.
- Only places that have been wired in see retry: `manager_parsing`
  and `mcp_tool_normalization`. Decoders elsewhere fall back to the
  one-shot `json.loads` path.

## Related

- Source: `src/bernstein/core/tasks/schema_retry.py`
- Wired in: `core/orchestration/manager_parsing.py`,
  `core/protocols/mcp/mcp_tool_normalization.py`
- PR #998, ticket `2026-04-30-feat-schema-validation-retry.md`
