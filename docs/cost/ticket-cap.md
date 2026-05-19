# Per-ticket cost cap

A per-ticket cost cap stops a runaway agent loop from spending more than
a stated USD budget on a single tracker ticket. When the cap is reached
the agent terminates cleanly at the next tool-call boundary, writes the
partial state to ``.sdd/runtime/halted/<ticket-id>.json``, and best-effort
posts a summary comment back to the originating tracker.

The cap is off by default. Existing run-wide budget enforcement
(``CostTracker.budget_usd`` / ``hard_budget_usd``) continues to apply
unchanged.

## Configure

Set ``cost_cap_usd`` on the ticket frontmatter handled by the tracker
adapter. Any positive value enables enforcement; ``None`` (the default)
keeps existing behaviour.

```yaml
# Tracker payload
id: ORG-123
cost_cap_usd: 2.50   # halt once cumulative spend reaches $2.50
```

A cap of ``0.0`` is honoured as "halt immediately" and is useful for
dry-run / preview surfaces that must not issue any tool call.

## Resolution order

``resolve_ticket_cap_usd`` picks the effective cap from layered sources:

| Source                                          | Notes                                |
|-------------------------------------------------|--------------------------------------|
| Ticket frontmatter (``cost_cap_usd``)           | Wins when set.                       |
| Per-tracker / per-role / per-priority overrides | Lookup by ``override_key``.          |
| Global default                                  | Optional fall-through.               |

## What happens when the cap trips

1. ``TicketCostCapMeter.should_halt`` flips ``True`` so the dispatch
   loop refuses to issue the next tool call.
2. The meter persists a ``HaltState`` record to
   ``.sdd/runtime/halted/<ticket-id>.json`` containing ``cost_usd``,
   ``cap_usd``, ``reason``, ``last_tool_call_id``, ``partial_artefacts``,
   ``timestamp``, and ``run_id``.
3. The orchestrator calls ``post_writeback_comment`` so the tracker
   adapter receives a structured comment of the form:

   ````yaml
   cost_used_usd: 1.2345
   cost_cap_usd: 1.0000
   stage_reached: tool-call-42
   reason: per_ticket_cost_cap_exceeded
   next_step_hint: review partial state under .sdd/runtime/halted/, raise the cap if the work item warrants more budget, then re-queue.
   ````

4. The agent exits with ``EXIT_CODE_TICKET_COST_CAP = 64``.

Writeback failures are logged and swallowed: a clean termination must
not be blocked by a temporarily unreachable tracker.

## API entry points

| Symbol                                         | Purpose                                        |
|------------------------------------------------|------------------------------------------------|
| ``bernstein.core.cost.TicketCostCapMeter``     | Per-ticket meter that drives the soft-abort.   |
| ``bernstein.core.cost.HaltState``              | Frozen halt-state record persisted to disk.    |
| ``bernstein.core.cost.CostCapExceeded``        | Exception raised by the dispatch loop adapter. |
| ``bernstein.core.cost.resolve_ticket_cap_usd`` | Layered cap resolution.                        |
| ``bernstein.core.cost.write_halt_state``       | Atomic JSON persistence under ``.sdd/runtime/halted/``. |
| ``bernstein.core.cost.post_writeback_comment`` | Best-effort tracker writeback.                 |

## Operator notes

- Re-queue a halted ticket by raising its ``cost_cap_usd``, removing
  the ``.sdd/runtime/halted/<ticket-id>.json`` file, and putting the
  ticket back on the queue.
- The halt-state file is content-addressable by ticket id; a sanitiser
  replaces filesystem-unsafe characters so any tracker id resolves to a
  valid filename.
- For unattended workloads, wire ``post_writeback_comment`` to a
  notification channel by passing a custom ``writeback`` callable to
  ``TicketCostCapMeter.enforce``.
