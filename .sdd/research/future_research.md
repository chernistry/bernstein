# Bernstein Roadmap Research — Deep Research Prompt

## Context

Bernstein is an open-source multi-agent orchestration system for CLI coding agents (Claude Code, Codex, Gemini CLI, Qwen, any CLI). Python 3.12+, deterministic orchestrator (no LLM tokens on coordination), short-lived agents, file-based state. PolyForm Noncommercial license.

**Already built (v0.1, March 2026):**
- Adapters: Claude, Codex, Gemini, Qwen, Generic (any CLI)
- Task server with REST API, dependency graph, priority routing
- Model/effort routing per task (opus for architecture, haiku for boilerplate)
- Pluggy-based plugin system (6 hook points)
- Prometheus /metrics endpoint
- Real-time web dashboard (HTMX + Tailwind + SSE)
- Multi-repo workspace orchestration
- GitHub App webhook integration (issues/PR/push -> tasks)
- Pluggable storage backends (memory/PostgreSQL/Redis)
- Self-evolution: analyzes metrics, proposes and applies improvements
- Agent catalogs (hire specialists from registries)
- Process visibility (bernstein-worker + bernstein ps)
- Trace viewer and replay (bernstein trace / bernstein replay)
- CI self-healing with bernstein doctor
- 2400+ tests, pyright strict 0 errors

**Goal:** Make the creator (Alex Chernysh) famous in the AI engineering space. Bernstein should become THE reference open-source agent orchestrator — the "Kubernetes of AI agents". Revenue path: open-source adoption -> consulting/enterprise -> hosted SaaS.

---

## Research Questions

Answer each section with SPECIFIC, ACTIONABLE findings. For each finding, rate:
- **Impact** (1-5): how much this moves the needle for adoption/revenue
- **Effort** (S/M/L/XL): implementation complexity
- **Urgency** (now/Q2/Q3/2027): when this becomes table stakes

### 1. Competitor Deep Dive

For EACH of these frameworks, provide:
- Current feature set (what they do TODAY, not roadmaps)
- GitHub stars, contributors, last release date
- Pricing model (free/paid/enterprise)
- Biggest strengths and weaknesses
- What users love (Reddit, HN, Twitter sentiment)
- What users hate (GitHub issues, complaints)

Frameworks to analyze:
- CrewAI
- AutoGen (Microsoft)
- LangGraph / LangChain
- OpenAI Agents SDK (formerly Swarm)
- Google ADK (Agent Development Kit)
- Anthropic Agent SDK / Claude Code
- Devon (Cognition)
- Cursor / Windsurf agent mode
- Cline / Roo Code
- Ruflo
- Agency Swarm
- Semantic Kernel (Microsoft)
- AutoGen Studio
- MetaGPT
- ChatDev
- CrewAI Enterprise
- Any new players that emerged in 2026

### 2. Market Gaps & Opportunities

What does NO framework do well today? Specifically:
- Cross-agent memory and knowledge sharing
- Agent-to-agent communication protocols (A2A, MCP)
- Cost optimization and budget management
- Deterministic reproducibility of agent runs
- Agent evaluation and benchmarking
- Multi-model orchestration (mixing providers in one workflow)
- Self-healing and self-improvement
- IDE integration (VS Code, JetBrains, Neovim)
- CI/CD integration (GitHub Actions, GitLab CI)
- Mobile/edge deployment
- Air-gapped / on-prem enterprise deployment

### 3. Enterprise Requirements (2026-2027)

What do Fortune 500 companies need to adopt agent orchestration?
- Security: RBAC, SSO, audit logs, data residency
- Compliance: SOC2, HIPAA, GDPR, FedRAMP
- Observability: what metrics/traces/logs do they expect?
- Cost governance: budget limits, approval workflows, chargeback
- Multi-tenancy: team isolation, resource quotas
- SLA: uptime guarantees, error budgets
- Integration: Slack, Jira, ServiceNow, PagerDuty, Datadog

### 4. Developer Experience Trends

What makes developers CHOOSE and STAY with a framework?
- Onboarding time (zero to first task)
- Documentation quality benchmarks
- Community health indicators
- Plugin/extension ecosystem
- CLI vs GUI vs API preferences
- Framework-agnostic vs opinionated tradeoffs

### 5. Technology Trends (2026-2027)

What's coming that Bernstein should prepare for?
- Model context windows growing (1M+ tokens)
- Multimodal agents (vision, audio, code)
- Agent-computer interfaces (browser, desktop, mobile)
- MCP (Model Context Protocol) evolution
- A2A (Agent-to-Agent) protocol adoption
- Agentic RAG patterns
- Code generation accuracy improvements
- Agent sandboxing and isolation advances
- Edge/local model deployment (Ollama, llama.cpp)
- WebAssembly for agent sandboxing

### 6. Monetization Models

How do successful open-source AI tools make money?
- Examples: LangChain -> LangSmith, Hugging Face -> Hub/Pro, Weights & Biases
- What converts free users to paid?
- Pricing tiers that work
- Enterprise sales cycle for AI tools
- Developer advocacy / content marketing ROI

### 7. Community Building

What drives open-source AI project adoption?
- Most effective launch channels (HN, Reddit, Twitter/X, YouTube, Discord)
- Content that generates stars (blog posts, demos, benchmarks)
- Conference talks that lead to adoption
- Partnerships that matter
- Timing considerations (when to launch publicly)

---

## Output Format

For each finding, structure as:

```
### [Finding Title]
**Impact:** 1-5 | **Effort:** S/M/L/XL | **Urgency:** now/Q2/Q3/2027
**Source:** [URL or reference]

[2-3 sentence description of the finding]

**Bernstein implication:** [What specific feature/change this suggests]
**Ticket suggestion:** [One-line ticket title]
```

At the end, provide a RANKED LIST of the top 50 suggested tickets, ordered by:
1. Impact * Urgency (highest first)
2. Effort (smallest first, as tiebreaker)

Format each ticket as:
```
[PRIORITY] #NNN — Title
Role: backend/frontend/architect/devops/docs/security
Scope: small/medium/large
Why: one sentence justification
Depends on: #NNN (if any)
```

Number tickets starting from #600 (to continue from existing backlog).

---

## Important Notes

- Be SPECIFIC, not generic. "Add better security" is useless. "Add RBAC with team-scoped API keys and JWT rotation" is useful.
- Cite REAL sources with URLs — actual GitHub issues, real Reddit threads, actual Gartner/Forrester quotes.
- Focus on what's ACHIEVABLE by a small team (1-3 people) within 6 months.
- Prioritize features that are VISIBLE and DEMONSTRABLE — things that make good demos, blog posts, and conference talks.
- Think about what creates NETWORK EFFECTS — features where more users = more value (catalogs, plugins, shared templates).
- Consider the "famous developer" angle — what positions Alex as a thought leader, not just a tool builder?
- Separate HYPE from REAL DEMAND — don't suggest features just because they sound cool. Suggest features because real users are asking for them.
