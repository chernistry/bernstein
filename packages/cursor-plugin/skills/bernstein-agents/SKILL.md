---
name: bernstein-agents
description: >
  Manage Bernstein agents — list active agents, inspect their output,
  kill stalled agents, or stream live logs. Use when the user asks about
  agents, wants to see what an agent is doing, or needs to kill one.
---

# Bernstein Agent Management

Inspect, monitor, and control active Bernstein agents.

## When to Use

- User asks "what agents are running?" or "show me the agents"
- User wants to see what a specific agent is working on
- User says "kill that agent" or "stop the backend agent"
- User asks "why is that agent stuck?" or wants to inspect agent output
- User wants to see agent logs

## Instructions

### List agents

1. Run `scripts/agents.sh list` to get all active agents.
2. Present them clearly:

```
## Active Agents (3)

| Agent | Role | Model | Status | Task | Runtime | Cost |
|-------|------|-------|--------|------|---------|------|
| ses-a1b2 | backend | claude-sonnet-4 | alive | TASK-042: Fix auth | 4m 12s | $0.32 |
| ses-c3d4 | qa | gpt-4.1 | alive | TASK-043: Write tests | 2m 45s | $0.18 |
| ses-e5f6 | frontend | claude-sonnet-4 | stalled | TASK-044: Update UI | 8m 03s | $0.51 |
```

### Inspect agent

3. To see what an agent is doing: `scripts/agents.sh logs <session_id>`
4. Show the last ~20 lines of output.

### Kill agent

5. To kill a stalled or misbehaving agent: `scripts/agents.sh kill <session_id>`
6. Confirm: "Agent ses-e5f6 terminated. Task TASK-044 returned to open queue."

### Stall detection

7. If any agent shows `stalled` status, proactively suggest killing it.
8. An agent is stalled if it hasn't sent a heartbeat in >60 seconds.
