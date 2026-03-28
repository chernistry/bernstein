# Reddit Post: r/LocalLLaMA

**Subreddit:** r/LocalLLaMA
**Title:** I built a multi-agent orchestrator that works with any CLI coding agent (Claude Code, Codex, Gemini, Qwen)
**When to post:** Day 4 of launch, after HN

---

## Post

I got tired of babysitting one AI coding agent at a time. So I built Bernstein — a multi-agent orchestrator that spawns parallel CLI agents and verifies the output.

**What it does:**

You give it a goal. It decomposes the goal into tasks, spawns one agent per task (in parallel, each in an isolated git worktree), runs a verification pass, and commits the result.

```bash
bernstein -g "Add JWT auth, write tests, update docs"
```

Three agents. 47 seconds. $0.42. CI pass rate 80% (vs. 52% for a single agent on the same tasks).

**The part that's relevant to this sub:**

The orchestrator is completely model-agnostic. It runs any CLI agent that accepts a prompt and writes files. Current adapters: Claude Code, Codex CLI, Gemini CLI, Qwen CLI. If you're running a local model via Ollama that has a CLI wrapper, it should work with a custom adapter (the interface is simple).

You can mix agents in a single run. Backend task → Claude Sonnet. Tests → Haiku. Docs → local Qwen. The routing logic is a Python function you can override.

**Architecture decision worth discussing:**

The orchestrator is deterministic Python — no LLM on coordination. Scheduling is a priority queue over a dependency graph. I tried LLM-based scheduling first and it was slow, expensive, and inconsistent. This approach is less flexible but the failures produce Python tracebacks, not LLM reasoning to decode.

**What it doesn't do (yet):**

- Agent-to-agent communication
- Dynamic re-planning mid-run
- Full local-only operation (the goal decomposition step still calls an LLM; the agents themselves can be local)

Benchmarks, architecture writeup, and comparison with other tools are in the repo.

**GitHub:** [link]

Happy to answer questions about the local model integration or the architecture.

---

## Comments to anticipate

**"How does it compare to AutoGen / CrewAI?"**
Different use cases. Those tools are great for conversational/research agents. Bernstein is optimized for code shipping — deterministic orchestration, git-native, CI integration. Not trying to replace them.

**"Why not just use a shell script?"**
Fair question. The value is in the verification pass (tests + linter + regression check) and the git worktree isolation. A shell script can parallelize, but without the safety checks you're merging blind.

**"Does it work with [local model X]?"**
It needs a CLI wrapper. If your model has one, the adapter is ~50 lines of Python. We'd welcome a contribution.

**"What's the overhead of the orchestrator itself?"**
Negligible. It's a Python process managing subprocess spawning. The bottleneck is always the agents.
