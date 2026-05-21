# Bernstein — Competitive Landscape Audit

> Snapshot date: **2026-05-12**. Star counts from `gh api repos/<owner>/<name>` at audit time.
> Bernstein itself: **321 stars, 36 forks** (sipyourdrink-ltd/bernstein).

---

## TL;DR — are we cooked?

⚠️ **Partially cooked, not dead.** The "deterministic orchestrator for many CLI agents" space is now mainstream and crowded. Three competitors have 10×–150× more stars than us. But none of them is the same shape as Bernstein.

- ❌ We lost the **mindshare race** for "parallel Claude Code sessions" (claude-flow 49k, vibe-kanban 26k, Archon 21k, claude-squad 7.4k, AO 7k).
- 🟠 Nobody else combines: **deterministic Python scheduler + 40+ adapters + HMAC audit chain + MCP-server-first + signed lineage**. Wedge survives.
- ✅ README is comparing against the **wrong generation** (LangGraph/CrewAI). It should benchmark against Archon, AO, claude-flow, conductor.build, vibe-kanban.
- ⚠️ One concrete loss: Archon's "workflow YAML" is the exact same idea as Bernstein PR #1117, but Archon has 21k stars and shipped first by mindshare. We did NOT get stolen from — they shipped in parallel; we're the smaller player and have to position around them.

**Play:** narrow positioning to **regulated / on-prem / audit-required** (DORA, NIS2, EU AI Act Art. 12). Stop competing for "fastest TUI for parallel Claude". Update README/landing to name modern competitors honestly. Steal multi-phase review pipeline from ralphex and self-healing watchdog from amux.

---

## Tier 1 — direct competitors (same shape)

These spawn parallel CLI coding agents from a single orchestrator. Same wedge.

| Project | Stars | What they do better | What we do better | Status |
|---|---|---|---|---|
| **ruvnet/ruflo** (claude-flow v3) | 49,331 | Brand, distribution, swarm intelligence + RAG marketing, 314 MCP tools | Deterministic non-LLM scheduler; HMAC audit; 40+ adapter breadth; on-prem-only | 🔴 ahead of us |
| **BloopAI/vibe-kanban** | 26,164 | Kanban UI for parallel agents; broad agent support; "drag to in-progress" UX is sticky | Deterministic scheduler; signed lineage; air-gap; CI autofix daemon; MCP server mode | 🔴 ahead of us (UX/stars) |
| **coleam00/Archon** | 21,309 | YAML workflows shipped first + 21k stars; "harness builder" framing; web UI; chat-platform integration | 40+ adapters vs Archon's smaller agent list; HMAC audit; deterministic scheduler (no LLM in loop); Python-native lib + MCP-server mode | 🔴 ahead of us (mindshare) |
| **smtg-ai/claude-squad** | 7,426 | Polished Go TUI; fastest install (`brew install claude-squad`); tmux-native | Deterministic plan execution; multi-stage verifier; regulated-deploy story; YAML plans; 40+ adapters | 🟠 par (different niche) |
| **ComposioHQ/agent-orchestrator** | 6,971 | Funded (Composio.dev); CI fixer + PR autofix; TypeScript dashboard; brand discord | Python-native; MCP server; HMAC audit; deterministic scheduler; broader adapter coverage; on-prem-only | 🟠 par |
| **generalaction/emdash** | 4,337 | YC W26 backing; polished Electron ADE; SSH-remote dev; 23 agents | Headless/CI-first; Python lib + MCP server (theirs is desktop app only); deterministic plan; signed lineage | 🟠 par (different surface — they're desktop, we're CLI/lib) |
| **stravu/crystal → Nimbalyst** | 3,051 | Electron app; diff-first review; rebrand to Nimbalyst with active push | We're not deprecated; CLI/lib/MCP shape suits CI; deterministic scheduling | 🟢 we're ahead (they shifted to closed/managed) |
| **njbrake/agent-of-empires (AoE)** | 2,180 | Mozilla.ai backing; phone access via Tailscale Funnel; PWA | Wider adapter coverage; deterministic scheduler; signed audit; Python ecosystem | 🟢 we're ahead on regulated/audit story |
| **umputun/ralphex** | 1,137 | Multi-phase review pipeline (5→codex→2 review agents) is genuinely good; single Go binary; zero setup | Multi-agent breadth (they do Claude only); MCP server; broad adapter coverage; CI/autofix daemon | 🟢 we're ahead on breadth |
| **awslabs/cli-agent-orchestrator** | 564 | AWS Labs backing; hierarchical LLM supervisor; tmux native | No LLM in scheduler; Python-native lib; MCP server first; signed audit; 40+ adapters | 🟢 we're ahead (different philosophy) |
| **conductor.build** | n/a (closed Mac app) | Mac-native polish; "diff-first" review UX | We're cross-platform CLI/lib; on-prem-only; deterministic plan replay | 🟠 par (different surface) |

**Reality check on "did they steal Bernstein's ideas":**
- ❌ No. Bernstein went public Feb 2026. Most of these were already public or shipped within weeks of us.
- ⚠️ **Convergent evolution** on git-worktree-per-agent + deterministic-ish scheduler. Everyone landed there because it's the obvious right answer. Archon's "deterministic and repeatable" tagline is identical to ours — but parallel discovery, not theft.

---

## Tier 2 — adjacent / sub-feature competitors

These cover a slice we also cover, but not the whole orchestration loop.

| Project | Stars | Slice | Threat to us |
|---|---|---|---|
| **wshobson/agents** | 35,243 | Claude Code subagent catalog (definitions, not orchestration) | Low — orthogonal; we could ingest their catalog like we do agency-agents |
| **msitarzewski/agency-agents** | (~250) | Multi-tool agent persona catalog (already integrated as `AgencyProvider`) | ✅ Already an input source for us |
| **mixpeek/amux** | 178 | Self-healing tmux watchdog for Claude Code; PWA; kanban + REST | Low — single-agent multiplexer, not multi-CLI orchestration. Their self-healing is worth stealing |
| **mco-org/mco** | 339 | "Consensus" mode — same prompt to N agents, synthesize results | Niche; could be a Bernstein workflow node |
| **Open-ACP/OpenACP** | 358 | Telegram/Discord/Slack bridge to coding agents via ACP | Overlap with our `bernstein chat serve`; they got there first on ACP |
| **nwiizo/ccswarm** | 139 | tmux + worktree + Claude Code orchestration | Low — small, sparse activity |
| **Kiro** (AWS) | (closed) | IDE + CLI with specs/hooks/MCP | Adjacent — they're an IDE, we adapter into them |
| **Anthropic "Agent Teams" (experimental)** | — | Built-in Claude Code multi-agent (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) | Long-term existential threat: if Anthropic ships first-party multi-agent, the "orchestrate Claude" wedge shrinks. Our defense: multi-vendor (40+ adapters) and audit chain. |
| **Steve Yegge / Gas Town** | — | "Kubernetes for agents" agentic IDE | Watch list — not shipped broadly |
| **Cursor Background Agents / OpenAI Codex parallel sandboxes** | — | Vendor-native parallel execution | Pulls air out of the room for "I just want parallel Claude on one machine" use case |

---

## What to STEAL — ranked by payoff/effort

### 🔥 High-payoff, low-effort

| Idea | Source | Pointer | Why |
|---|---|---|---|
| **Multi-phase review pipeline** (N reviewers in parallel → critic → N more) | umputun/ralphex | `/Users/sasha/IdeaProjects/cloned/ralphex/README.md` "Phase 2: First Code Review" | We have `quality/` gates but not a *parallel-reviewer fan-out → critic synthesis* node. Ship as a stock workflow |
| **Self-healing watchdog** (auto-`/compact` near context limit, restart on `redacted_thinking` errors) | mixpeek/amux | `/Users/sasha/IdeaProjects/cloned/amux/README.md` "Self-Healing Watchdog" table | Concrete table of conditions → actions. Drop in `core/observability/` |
| **Kanban-board task surface** | vibe-kanban, AoE | their READMEs | Our TUI already has task list; add drag-to-status semantics + per-card diff peek |
| **Diff-first review UX** in the dashboard | conductor.build, emdash, Crystal | n/a (product surfaces) | Make `bernstein live` open worktree diff inline; Crystal popularised this |

### ⚠️ Medium-effort

| Idea | Source | Why |
|---|---|---|
| **Consensus / "ask N agents, synthesize"** as a workflow node | mco | Different from our cross-model review — same prompt to N agents, compare outcomes, vote/synthesize. Could be a new `workflows/consensus.yaml` |
| **PWA / mobile remote control** | amux, AoE | We have `bernstein chat serve` and `bernstein tunnel`. A PWA over the web dashboard is incremental |
| **Workflow marketplace** | Archon (`.archon/workflows/`) | Archon's "commit workflow YAML to your repo" framing won mindshare. Push harder on `workflow init` + a public template index |

### 🧊 Low-priority

| Idea | Source | Why deprioritise |
|---|---|---|
| Electron desktop app | Crystal/emdash | Wrong surface for us. Stay CLI/lib/MCP — that is our differentiator |
| LLM-supervisor scheduler | awslabs/cao | Deliberately the opposite of our deterministic-scheduler wedge. Do not chase |

---

## Strategic recommendation

**Narrow, don't pivot.** The pure "parallel Claude on my Mac" market is owned by claude-flow / vibe-kanban / Archon / claude-squad and we will not catch them on stars. Stop trying.

**What we own, defensibly:**

1. **Regulated / on-prem / audit-required deployments.** HMAC audit chain, signed agent cards, signed lineage, EU AI Act Art. 12 export, air-gap profile, customer-key signing — none of the Tier 1 competitors have this. This is a real moat for the "we sell to banks / health / gov" customer.
2. **Python library + MCP server primitive.** Everyone else is a CLI or desktop app. We are the only one that imports cleanly into a Python codebase AND exposes itself over MCP. That makes us a building block for other people's orchestrators, not just an end-user tool.
3. **Adapter breadth (40+).** Claude-flow, claude-squad, AO, Crystal, emdash all max out around 20–25 agents. We have 43. Keep widening — every new adapter is a moat brick.

**What to drop:**

- Stop benchmarking against LangGraph/CrewAI/AutoGen in the README. That comparison is *technically* fine but **wrong generation** — the user who looks up "claude code orchestrator" in May 2026 has never heard of LangGraph and has heard of claude-flow.
- Drop the "fastest TUI" energy. We will lose to claude-squad and emdash on polish forever; we are not an Electron team.
- Stop advertising self-evolution as a top-line feature. It's experimental and reads as gimmick next to claude-flow's "self-learning swarm intelligence" marketing.

**Verdict:** Bernstein is **not dead**, but the README is fighting last year's war. Reposition as **"the orchestrator your compliance team will sign off on"** and the picture flips from "300 stars vs 26k" to "the only one that ships with a regulator-ready audit export."

---

## README / landing — comparison table fix

The current README has two `how it compares` tables:

| Table | Current state | Action |
|---|---|---|
| **how it compares** (top) | Compares to Archon (only modern competitor) + LangGraph (wrong generation) | ✅ Keep Archon; ❌ drop LangGraph here; ➕ add claude-flow + vibe-kanban + Composio AO |
| **detailed comparison** (further down) | Compares to CrewAI, AutoGen, LangGraph | ⚠️ Keep but **demote** — move below the new CLI-orchestrator table; relabel as "previous-generation Python multi-agent frameworks (for completeness)" |
| **CLI-coding-agent orchestrators** table | Compares to AWS cao, Composio AO, emdash, ralphex | ✅ Already exists and is good; ➕ add claude-flow (Ruflo), vibe-kanban, claude-squad, Archon, conductor.build |

**Concrete new top comparison** to land on the README (≤6 rows, scannable):

| Feature | Bernstein | Archon (21k) | claude-flow (49k) | claude-squad (7.4k) | Composio AO (7k) | vibe-kanban (26k) |
|---|---|---|---|---|---|---|
| LLM in scheduling loop | no | partial | yes (swarm) | no | yes | yes |
| Adapter count | 43 | ~10 | ~5 | ~5 | 3 | ~6 |
| Signed audit chain | yes | no | no | no | no | no |
| MCP server mode | yes | yes | yes (314 tools) | no | no | no |
| Air-gap / on-prem profile | yes | partial | no (cloud-leaning) | no | no | no |
| Library (Python import) | yes | no | no | no | no | no |
| Primary surface | CLI + lib + MCP | CLI + web | CLI + web | TUI | CLI + dashboard | desktop board |

**Landing page** (`bernstein.run`): replace the LangGraph/CrewAI sales-y compare with this table verbatim. The honest comparison is more credible than the framework-grade-1 fight we're currently picking.

---

## Operator follow-ups

- ⏳ Update `README.md` top comparison table + demote LangGraph/CrewAI block. Owner: docs role.
- ⏳ Update `bernstein.run` landing comparison block to match.
- ⏳ Ship `workflows/multi-phase-review.yaml` (steal ralphex's pipeline).
- ⏳ Spike `core/observability/watchdog.py` (steal amux's auto-compact + restart).
- ⏳ Decide: kill or keep self-evolution top-line marketing.
- ⏳ Reposition top-line tagline from "orchestrate any AI coding agent" to "**the orchestrator your compliance team will sign off on**" (or similar; needs marketing pass).

## Sources

Star counts: live `gh api` calls 2026-05-12. Searches: `claude code orchestrator`, `multi-agent CLI orchestrator`, `claude code parallel agents`, `git worktree agent orchestrator`. READMEs read locally under `/Users/sasha/IdeaProjects/cloned/`.
