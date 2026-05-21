# Bernstein vs. Stoneforge

> **tl;dr** -- Stoneforge is a **provider-agnostic** web dashboard that orchestrates Claude Code, OpenCode, and OpenAI Codex agents in isolated git worktrees with a steward-style merge queue. Bernstein is a **CLI-first** orchestrator that wraps 39+ CLI agents with deterministic Python scheduling, an external janitor, and budget-capped headless runs. The real question is whether you want a browser tab open while agents work, or whether you want to point a terminal at the project and walk away.

*Last verified: 2026-05-04 against [github.com/stoneforge-ai/stoneforge](https://github.com/stoneforge-ai/stoneforge) (v1.24.0, Apache 2.0, 138 stars).*

---

## What Stoneforge actually is

Stoneforge is a web dashboard and runtime for orchestrating AI coding agents. It runs locally on `http://localhost:3457` and exposes:

- a Kanban board, planning view, and real-time agent activity feed
- an in-browser code editor (Monaco + LSP)
- a merge-request review queue staffed by **steward agents** that run tests, squash-merge on green, and emit handoff tasks on red
- channel-based messaging and a document library shared across agents

Under the hood: each worker gets its **own git worktree** (no shared session), event-sourced state in JSONL with a SQLite cache for query speed, and provider selection per-agent across **Claude Code (default), OpenCode, and OpenAI Codex**. Authentication is delegated to whichever underlying agent CLI you point it at -- Stoneforge never holds provider keys directly.

---

## When to pick Stoneforge

- **You want to watch.** A browser dashboard with a Kanban board and live activity feed is genuinely better UX than tailing logs when you're actively monitoring agents.
- **You want a shared knowledge base across agents.** Stoneforge's channel/document model lets agents read each other's notes; Bernstein keeps each agent's context isolated by design.

## When to pick Bernstein

- **You want unattended runs.** `bernstein --headless --budget 20` runs to backlog-empty or budget-exhausted with no UI to keep open. Stoneforge's web dashboard is its primary interface.
- **You need broader CLI coverage.** Bernstein wraps 39+ adapters (Claude, Codex, Gemini, Cursor, Aider, Amp, Kilo, Kiro, Goose, OpenCode, Qwen, Cody, Continue, Ollama, IAC, Copilot, Droid, Cline, Codebuff, Hermes, Auggie, Kimi, Rovo, Pi, Mistral, AutoHand, Forge, Plandex, OpenHands, OpenInterpreter, AIChat, GPTMe, Charm, Composio, Letta Code, Ralphex, generic, plus Claude tier variants). Stoneforge supports three providers.

---

## Side-by-side

| Dimension | Bernstein | Stoneforge |
|---|---|---|
| **Primary interface** | CLI + TUI + REST API | Web dashboard at `localhost:3457` |
| **Provider coverage** | 39+ CLI adapters | 3 (Claude Code, OpenCode, OpenAI Codex) |
| **Agent isolation** | Per-task worktree, fresh process, exits after 1-3 tasks | Per-worker worktree, persistent worker session |
| **Coordination model** | Deterministic Python scheduler, zero LLM tokens on coordination | Event-sourced (JSONL + SQLite cache), steward-driven merge queue |
| **Verification** | External janitor: lint, tests, type-check, custom gates | Steward agent runs tests; merges on pass, handoff task on fail |
| **Persistence** | `.sdd/` files in repo (WAL, CAS store, backlog YAMLs) | JSONL event log + SQLite cache; survives restarts |
| **Headless / overnight** | Native (`--headless --budget`) | Possible but the UI is the design center |
| **License** | Apache 2.0 | Apache 2.0 |
| **Maturity / scale** | v1.9.x, multi-thousand stars | v1.24.0 (Apr 2026), 138 stars |

---

## Architecture

**Stoneforge:**

```
Web dashboard (localhost:3457)
    |
    v
Stoneforge runtime  ----  event log (JSONL) + SQLite cache
    |
    +-- Worker 1 (Claude Code)  in worktree-1   ---+
    +-- Worker 2 (OpenCode)     in worktree-2   ---+--> Steward agent --> merge queue
    +-- Worker 3 (Codex)        in worktree-3   ---+
```

**Bernstein:**

```
bernstein -g "goal"   (terminal)
    |
    v
Task server (deterministic Python, .sdd/ files)
    |
    +-- Task A -> claude  (isolated worktree, fresh context) -> janitor -> merge
    +-- Task B -> codex   (isolated worktree, fresh context) -> janitor -> merge
    +-- Task C -> gemini  (isolated worktree, fresh context) -> janitor -> merge
```

Both projects converge on per-worker git worktrees as the isolation primitive. They diverge on what sits above: Stoneforge's dashboard + steward + persistent workers vs Bernstein's deterministic scheduler + janitor + short-lived agent processes.

---

## Notes on a previous version of this page

Earlier drafts of this comparison described Stoneforge as "provider-integrated", "single-provider", and "IDE-integrated" with VS Code and JetBrains plugins. None of that matches the current upstream:

- Stoneforge is **provider-agnostic** -- per-agent selection across Claude Code, OpenCode, and Codex.
- It is a **web dashboard** at `localhost:3457`, not an IDE plugin. There are no VS Code or JetBrains extensions in the repo.
- Agents run in **isolated worktrees**, not a shared provider session. Coordination happens via the event log + steward agent, not via a shared context window.

The "provider lock-in" framing in the previous page therefore did not apply to Stoneforge. This rewrite drops it.

---

## Cost comparison

Bernstein routes simple tasks to cheaper Claude tiers (Haiku) and reserves expensive tiers for high-complexity work via the **cascade router** (`core/routing/cascade_router.py`). Effective per-task cost in mixed workloads is typically lower than running every task on a single premium tier.

Stoneforge cost is determined by whichever provider you pick for each agent; there is no built-in cross-tier cost optimization within a single provider. Picking the right model per task is up to the operator. For a team that already standardizes on one provider per project, this is fine; for a workload that mixes complexity, Bernstein's bandit pays for itself.

Either tool's cost model is dominated by which models you use, not by orchestration overhead.

---

## Conclusion

Both projects are honest about what they are. Stoneforge is the right pick when a human is actively in the loop and the project benefits from a shared knowledge surface across agents. Bernstein is the right pick when the run must complete unattended, on a CI runner, or across an adapter set that goes beyond the three providers Stoneforge currently supports.

---

## See also

- [compare/README.md](./README.md)
- [bernstein-vs-single-agent.md](./bernstein-vs-single-agent.md)
- [Stoneforge upstream](https://github.com/stoneforge-ai/stoneforge)
