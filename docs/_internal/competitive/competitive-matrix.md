# Feature Comparison

How Bernstein's feature set lines up against other multi-agent frameworks, as of April 2026.

## Feature comparison

| Feature | Bernstein | CrewAI | AutoGen | LangGraph | OpenAI Agents SDK | Google ADK |
|---|---|---|---|---|---|---|
| **Model agnostic** | Yes — any CLI agent (Claude Code, Codex, Gemini CLI, Kiro, OpenCode, Qwen) | Yes — LiteLLM integration | Yes — supports OpenAI, Azure, local models | Yes — via LangChain model abstraction | No — OpenAI models only | No — Gemini models only |
| **CLI agent support** | Core design — wraps existing CLI tools directly | No — Python API agents only | No — Python API agents only | No — Python API agents only | No — API-based agents only | No — API-based agents only |
| **File-based state** | Yes — `.sdd/` directory, no database required | No — in-memory or requires external storage | No — in-memory conversation state | Requires checkpointer backend (SQLite, Postgres) | No — in-memory | No — in-memory or cloud state |
| **Self-evolving** | Yes — `--evolve` mode analyzes metrics, proposes and executes improvements | No | No | No | No | No |
| **Multi-provider routing** | Yes — routes tasks to different providers based on complexity, cost, health, quota, and role-pinned provider policy | Partial — can assign different models per agent | Partial — can configure per-agent models | Yes — can assign models per node | No — single provider | No — single provider |
| **Provider failover** | Yes — typed rate-limit detection retries alternate providers and requeues orphaned work safely | Partial — user-defined retries | Partial — custom logic required | Partial — graph author must implement | No | No |
| **Process isolation** | Yes — each agent runs in its own process and git worktree | No — threads or async within one process | Partial — agents share a Python runtime | No — runs within one Python process | No — API calls within one process | No — runs within one process |
| **Cost optimization** | Yes — epsilon-greedy bandit learns cheapest viable model per task type, model cascade on failure | No — manual model assignment | No — manual model selection | No — manual model selection | No — single model pricing | No — single model pricing |
| **Deterministic orchestrator** | Yes — zero LLM tokens on coordination | No — LLM-driven agent delegation | No — LLM-driven conversation routing | Partial — graph structure is deterministic, but nodes use LLMs | No — LLM-driven handoffs | No — LLM-driven orchestration |
| **Multi-turn conversation** | No — agents are short-lived (1-3 tasks, then exit) | Yes — agents converse in roles | Yes — strong multi-turn, multi-party conversations | Yes — stateful conversation graphs | Yes — built for conversation handoffs | Yes — multi-turn with session state |
| **Visual workflow builder** | No | No | AutoGen Studio provides a UI | LangGraph Studio provides a UI | No | No |
| **Ecosystem / integrations** | Adapters for major CLI agents; MCP server registry | LangChain ecosystem, many tool integrations | Microsoft ecosystem, Azure integration | Full LangChain/LangSmith ecosystem | OpenAI platform (tools, retrieval, code interpreter) | Google Cloud, Vertex AI, A2A protocol |
| **Open source license** | Apache 2.0 | Apache 2.0 | Apache 2.0 (with CC-BY-SA docs) | MIT | MIT | Apache 2.0 |

## Bernstein's design choices

**CLI-native, not API-native.** Bernstein wraps CLI tools you already have installed. If you can run `claude`, `codex`, `gemini`, `kiro`, or `opencode` in your terminal, Bernstein can orchestrate it.

**Deterministic orchestrator.** The orchestrator is regular Python code. Task assignment, scheduling, health checks, and retries are all standard control flow. LLM calls happen only inside the agents doing actual work.

**Disposable agents.** Each agent spawns fresh, works in an isolated git worktree, and exits. The janitor verifies each result (tests pass, files exist, no regressions) before merging. Response-cache reuse is verification-gated, so cached completions cannot bypass that safety bar.

**Learned cost optimization.** The epsilon-greedy bandit tracks which model gives the best success-rate-to-cost ratio for each task type. It starts by exploring, then converges on the cheapest model that still meets quality thresholds. Model cascade (cheap to expensive) handles failures automatically.

**Self-evolution.** Running `bernstein --evolve` analyzes metrics from past runs, identifies improvement opportunities, and generates upgrade proposals.

## Different design goals

The matrix above compares features, not fitness for purpose. Bernstein is purpose-built for multi-agent software development orchestration — multiple CLI coding agents working in parallel git worktrees on a codebase. Frameworks aimed at multi-turn agent conversations (AutoGen, CrewAI), graph-based stateful LLM apps (LangGraph), or platform-specific deployments (OpenAI Agents SDK, Google ADK) optimise for different workloads.
