# 333e — Dynamic Agent Scaling (Scale Up/Down Based on Queue)

**Role:** backend
**Priority:** 0 (urgent)
**Scope:** medium
**Depends on:** #333d

## Problem

Bernstein spawns a fixed number of agents (max_agents). If there are 20 open tasks, 5 agents work on them sequentially. If there are 2 tasks, 5 agents sit idle. The system should scale agents dynamically based on queue depth, budget, and task parallelizability.

## Design

### Auto-scaling rules
```python
# Scale UP when:
open_tasks > active_agents * 1.5  # queue is deep
budget_remaining > 50%             # budget allows it
parallelizable_tasks > active_agents  # tasks CAN run in parallel

# Scale DOWN when:
open_tasks < active_agents         # more agents than work
budget_remaining < 20%             # budget running low
```

### Max agents by plan tier
- Free tier (Gemini/ChatGPT login): max 3 agents (rate limits)
- Standard (API keys): max 6 agents (default)
- Premium (high rate limits): max 10 agents
- Config override: `max_agents: 15` in bernstein.yaml

### Burst mode
For parallelizable task batches (e.g., "write tests for 10 modules"), temporarily burst above max_agents for the batch duration, then scale back.

### Agent lifecycle
- Agents that finish their task AND no more tasks for their role → exit immediately (don't idle)
- Idle detection: if agent has been waiting >60s for a task → exit
- This frees slots for other roles

## Files to modify

- `src/bernstein/core/orchestrator.py` (scaling logic in tick)
- `src/bernstein/core/models.py` (ScalingPolicy dataclass)

## Completion signal

- Agent count scales with queue depth
- Agents exit when their role has no more work
- Budget-aware scaling (fewer agents when budget low)


---
**completed**: 2026-03-28 22:16:21
**task_id**: 10fd530a2754
**result**: Completed: 340 — Benchmark vs GitHub Agent HQ
