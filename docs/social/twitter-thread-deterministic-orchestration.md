# Twitter Thread: "Why deterministic orchestration beats LLM scheduling"

**Platform:** Twitter/X
**Goal:** Technical credibility — explain the architectural decision
**When to post:** Day 2 of launch

---

## Thread

**1/**
Most multi-agent frameworks use an LLM to decide what agent does what next.

I tried that. It was slow, expensive, and wrong 30% of the time.

Here's why I switched to deterministic orchestration and what I gave up to get there.

🧵

---

**2/**
The LLM-scheduling approach looks like this:

```
orchestrator_llm.decide(
    tasks=pending_tasks,
    agents=available_agents,
    context=full_history
)
```

Every scheduling decision burns tokens. With 10 tasks and 5 agents, you're calling the LLM 50+ times just on routing.

---

**3/**
The failure modes I saw:

- Scheduling LLM hallucinates a dependency that doesn't exist → agents block forever
- Scheduling LLM changes its mind mid-run → tasks get re-assigned inconsistently
- Debugging a failure means reading LLM reasoning traces, not stack traces

---

**4/**
So I replaced the scheduling LLM with a priority queue and a dependency graph.

```python
# not pseudocode — actual orchestrator core
ready = [t for t in tasks if deps_satisfied(t)]
ready.sort(key=lambda t: t.priority)
for task in ready[:max_parallel]:
    spawn(task)
```

That's the entire scheduling logic. It's 8 lines.

---

**5/**
What I gave up:

- Dynamic re-prioritization based on context ("this task is now more urgent because...")
- Emergent agent behavior ("agent decided to split the task")
- Flexibility when task structure is genuinely ambiguous

These are real tradeoffs. They matter for some use cases.

---

**6/**
What I gained:

- Scheduling is O(n log n), not O(n × LLM_latency)
- Failures produce Python tracebacks, not LLM reasoning to decode
- The orchestrator is deterministic — same inputs produce same execution order, every time
- Zero scheduling cost. All tokens go to the actual work.

---

**7/**
Where LLMs *do* live in Bernstein:

1. Goal decomposition (once, at start) — LLM breaks a natural-language goal into typed tasks
2. Inside each agent — doing the actual coding work
3. Janitor verification summaries — summarizing what passed/failed

LLMs do the thinking. Python does the coordination.

---

**8/**
The analogy: a project manager vs. a calendar.

A good PM adapts dynamically. A calendar is rigid but never wrong about what's scheduled.

For coding tasks with clear deliverables, you usually want the calendar. LLM-based scheduling shines when the work is genuinely unpredictable.

---

**9/**
This isn't a universal claim. CrewAI, AutoGen, and LangGraph make different tradeoffs for different use cases.

If you're building research agents or open-ended exploratory tasks, LLM orchestration makes sense. For code shipping, determinism wins.

---

**10/**
Full architecture writeup: [blog post link]

Bernstein is open source. The orchestrator core is ~200 lines of Python. Read it, fork it, disagree with my choices.

GitHub: [link]
