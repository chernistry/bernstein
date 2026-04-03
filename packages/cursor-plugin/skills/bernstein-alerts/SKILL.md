---
name: bernstein-alerts
description: >
  Show active alerts from Bernstein — failed tasks, stalled agents,
  budget warnings, blocked tasks needing human intervention.
  Use when the user asks about problems, errors, warnings, or what needs attention.
---

# Bernstein Alerts

Surface problems that need attention right now.

## When to Use

- User asks "any problems?" or "what needs my attention?"
- User asks about errors, failures, or warnings
- Something seems wrong and user wants a diagnostic
- User says "is anything broken?"

## Instructions

1. Run `scripts/alerts.sh` to fetch current alerts.
2. Categorize and present by severity:

```
## Alerts

### Critical
- Task TASK-042 FAILED: "TypeError in auth middleware" — agent ses-a1b2 (backend)
  → Fix: Review the error, create a follow-up task, or retry

### Warning
- Budget at 85% ($4.25 / $5.00) — consider increasing budget or pausing low-priority tasks
- Agent ses-e5f6 stalled for 3m 20s — consider killing it with /bernstein-agents

### Info
- 2 tasks blocked, waiting for human approval — use /bernstein-approve to review
```

3. For each alert, suggest a concrete action:
   - Failed task → offer to create a retry task or inspect the error
   - Stalled agent → offer to kill it
   - Budget warning → show cost breakdown
   - Blocked task → link to approval flow
