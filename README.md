<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="Bernstein" src="docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"To achieve great things, two things are needed: a plan and not quite enough time."* — Leonard Bernstein

</div>

### why the name?

Bernstein is named after Leonard Bernstein, the American conductor and composer. The project orchestrates a crew of CLI coding agents the way Bernstein conducted the New York Philharmonic — every player on cue, the score deterministic, the conductor accountable for the result. He is the original orchestrator the project takes its name from.

<div align="center">

### the orchestrator your compliance team will sign off on.

Multi-agent CLI orchestration with an HMAC-signed audit chain, signed agent cards, per-artefact lineage, and an air-gap deploy profile — for teams that have to show their work to a regulator.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![GHCR](https://img.shields.io/badge/ghcr.io-bernstein-2496ed?logo=docker&logoColor=white)](https://ghcr.io/sipyourdrink-ltd/bernstein)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)
[![CodeTrendy](https://img.shields.io/badge/CodeTrendy-listed-FBBF24)](https://codetrendy.com/listing/bernstein)

[website](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [docs](https://bernstein.readthedocs.io/) &middot; [install](docs/getting-started/install.md) &middot; [first run](docs/getting-started/first-run.md) &middot; [enterprise eval](docs/ENTERPRISE.md) &middot; [glossary](docs/reference/GLOSSARY.md) &middot; [limitations](docs/reference/KNOWN_LIMITATIONS.md) &middot; [sponsor](https://github.com/sponsors/chernistry)

</div>

---

Bernstein is a deterministic Python scheduler that runs a crew of CLI coding agents (Claude Code, Codex, Gemini CLI, and 40 more) against a single goal in parallel git worktrees, with an HMAC-signed audit chain over every step.

### at a glance

- **44 CLI agent adapters** ship in v1.10.7 — 41 third-party wrappers, 2 leaf-node delegators, plus a generic `--prompt` wrapper. Source of truth: the [supported agents](#supported-agents) table below.
- **HMAC-SHA256 audit chain** per [RFC 2104](https://datatracker.ietf.org/doc/html/rfc2104), one record per scheduling decision, tamper-evident. Operator guide: [docs/security/audit-log.md](docs/security/audit-log.md).
- **Signed agent cards** use detached JWS ([RFC 7515 §A.5](https://datatracker.ietf.org/doc/html/rfc7515#appendix-A.5)) over [RFC 8785 (JCS)](https://datatracker.ietf.org/doc/html/rfc8785) canonicalization, with [Ed25519 / EdDSA](https://datatracker.ietf.org/doc/html/rfc8037) keys. Code: [src/bernstein/core/security/agent_card_signer.py](src/bernstein/core/security/agent_card_signer.py).
- **Per-artefact lineage** records every file write linked back to producer + inputs + prompt SHA + model + cost; customer-key signing for DORA / NIS2 / EU AI Act Article 12 evidence. CLI: `bernstein lineage verify <run_id>`.
- **Deterministic scheduler**: zero LLM in the coordination loop. Plain Python decides who runs, where, with what budget. Replay yesterday's plan, get yesterday's task graph.

### why this exists

i wrote bernstein because i was paying $400/month in claude bills running three coding agents in parallel and getting nondeterministic merges.

as of 2026-05-13: 338 stars, 37 forks, ~3,769 pypi downloads/day (mostly bots; ~54k/month), apache 2.0, solo maintained, no funding. numbers will drift; the line above is the source-of-truth date — re-run `pip stats` / GitHub API to refresh.

### install in 30 seconds

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

## sponsor

if bernstein routed a model that saved you a claude bill, $25 covers a month of my coffee.

[github.com/sponsors/chernistry →](https://github.com/sponsors/chernistry)

tier ladder, escalation thresholds, and what each tier gets you live at [bernstein.run/sponsors](https://bernstein.run/sponsors?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme).

## who this is for

specific shapes where the value lands:

- engineering teams running ≥3 cli coding agents in parallel — each agent gets its own git worktree, the merge queue serialises landings, no race conditions
- regulated or on-prem environments — every routing decision is in plain text, the audit log is hmac-signed and tamper-evident, no saas hop, no third-party data plane
- platform teams that need an audit log of agent decisions — the orchestrator writes one row per scheduling decision, you can grep it
- anyone burning more than $1k/mo on cursor/aider/claude-max who wants determinism — you can replay yesterday's plan and get yesterday's task graph
- forward-deployed engineers dropping into a client repo — credentials stay in your env, not the client's; agents you spawn are whichever cli tool the client already trusts

if you nodded at two of those bullets, this fits.

## who this is NOT for

equally specific. these are the cases where you should pick something else:

- "i want one pair-programmer to chat with about my code" — claude code or cursor alone. bernstein adds orchestration overhead you don't need
- prototypes where merge gates are overkill — the lint/types/tests/cross-model-review pipeline is value when the cost of a bad merge is real, friction when you're throwing the repo away on friday
- non-coding tasks (research, writing, data analysis pipelines) — bernstein wraps cli coding agents specifically, not generic llm workflows. crewai or autogen are the right shape there
- anyone who wants a saas wrapper with a credit card form — bernstein is on-prem only by design. if you want managed, this is the wrong project, not the wrong fit
- teams that need a vendor with a support sla and a contract — solo open-source project. github issues are how support happens
- research-shape "let the agents collaborate emergently" use cases — the deterministic scheduler is a hard wall there

### how it compares

The honest read: Bernstein is the smaller player on stars in this category. What it has that the bigger ones don't is the auditability surface a regulated buyer needs — HMAC-chained audit, signed agent cards, per-artefact lineage with customer-key signing, and an air-gap deploy profile. If you're a compliance team writing the deployment review for a DORA / NIS2 / EU AI Act Article 12 environment, that's the column that matters.

| Feature                          | Bernstein       | claude-flow (49k⭐) | Archon (21k⭐) | vibe-kanban (26k⭐) | claude-squad (7.4k⭐) | Composio AO (7k⭐) |
|----------------------------------|-----------------|---------------------|----------------|---------------------|------------------------|--------------------|
| Hook they sell                   | regulated / on-prem / audit | swarm intelligence, hive-mind, 314 MCP tools | deterministic + repeatable workflow YAML, web UI | kanban board UI for parallel Claude | polished Go TUI, tmux-native | TypeScript dashboard, CI fixer |
| LLM in scheduling loop           | no              | yes (swarm)         | partial        | yes                 | no                     | yes                |
| CLI adapter count                | 44              | ~5                  | ~10            | ~6                  | ~5                     | 3                  |
| HMAC-chained audit log           | yes             | no                  | no             | no                  | no                     | no                 |
| Signed agent cards (detached JWS) | yes            | no                  | no             | no                  | no                     | no                 |
| Per-artefact lineage, customer-key signed | yes    | no                  | no             | no                  | no                     | no                 |
| Air-gap / on-prem profile        | yes             | no (cloud-leaning)  | partial        | no                  | no                     | no                 |
| MCP server mode                  | yes             | yes (314 tools)     | yes            | no                  | no                     | no                 |
| Python library (importable)      | yes             | no                  | no             | no                  | no                     | no                 |
| Primary surface                  | CLI + lib + MCP | CLI + web           | CLI + web      | desktop board       | TUI                    | CLI + dashboard    |

Star counts captured 2026-05-12; numbers drift. Source memo: [`docs/competitors.md`](docs/competitors.md). The "hook they sell" column is each project's own framing, not a Bernstein opinion.

Workflow YAML shipped in [PR #1117](https://github.com/sipyourdrink-ltd/bernstein/pull/1117) (merged 2026-05-08); plans are authored as YAML and validated by `bernstein workflow validate`. The longer feature matrix and the previous-generation Python multi-agent frameworks (CrewAI, AutoGen, LangGraph) are in the [Detailed comparison](#detailed-comparison) section below; that comparison is kept for completeness, but Bernstein and those projects are different shapes — they orchestrate Python LLM calls, Bernstein orchestrates CLI coding agents in git worktrees.

---

### what is this, in one paragraph

You tell Bernstein what you want built. It splits the work across several AI coding agents, runs them in parallel inside isolated git worktrees, records every handoff in an HMAC-SHA256-chained audit log (RFC 2104), runs the tests, and merges the code that actually passes. You come back to a green PR.

Forward-deployed engineering, on a swarm. Drop Bernstein into a client repo and you get a multi-agent crew with file-based state (`.sdd/`), per-agent credential scoping, and a signed audit trail running on whichever CLI agents the client already trusts.

> Cited as the "deterministic zero-LLM orchestration" pattern reference implementation in [nibzard/awesome-agentic-patterns](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) and "the most architecturally interesting tool" by Augment Code's [open-source agent orchestrators roundup (2026)](https://www.augmentcode.com/tools/open-source-agent-orchestrators).

### other install methods

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # macOS / Linux one-liner
irm https://bernstein.run/install.ps1 | iex             # Windows PowerShell
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

See the full [install matrix](#install) for `dnf copr`, `npx`, optional extras, and the wheelhouse path for air-gapped sites.

### why the scheduler is plain Python

Most agent orchestrators use an LLM to decide who does what. That is non-deterministic and burns tokens on scheduling instead of code. Bernstein does one LLM call to break down your goal, then the rest (running agents in parallel, isolating their git branches, running tests, routing retries) is plain Python. Every run is reproducible. Every step is logged and replayable.

No framework to learn. No vendor lock-in. Swap any agent, any model, any provider.

<img alt="Bernstein in action: parallel AI agents orchestrated in real time" src="docs/assets/in-action-small.gif" width="700">

What you see while it runs:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

### YAML workflow manifests (optional)

When the open-ended `bernstein run -g "<goal>"` is too coarse-grained, the
`bernstein workflow` family runs a declarative DAG of agent / command / loop
nodes. Manifests are plain YAML, validated up-front, and dispatched through
the same `AgentSpawner` the rest of Bernstein uses. No parallel spawn path,
no LLM in the scheduler.

```bash
bernstein workflow list                       # bundled + user-installed
bernstein workflow run idea-to-pr -g "Add JWT auth"
bernstein workflow init my-flow               # scaffold a starter manifest
bernstein workflow validate path/to/flow.yaml
```

Stock workflows that ship with the wheel:

| Name                  | What it does                                         |
| --------------------- | ---------------------------------------------------- |
| `idea-to-pr`          | research → plan → implement → tests → PR             |
| `refactor-with-tests` | find target → propose → implement → loop until green |
| `security-review`     | scan → triage → patch → adversary review             |
| `doc-update`          | audit → update → docs build                          |
| `dependency-bump`     | bump → install → tests-loop → smoke                  |
| `hot-fix`             | reproduce → fix → regression loop → changelog        |

Loop nodes re-fire until a bash predicate exits 0 (`pytest -x` is a typical
one). `fresh_context: true` mints a new agent session per iteration. The
`interactive: true` flag is reserved for the approval-gate work tracked in
ticket #1110 and currently raises a clear `NotImplementedError`.


## use cases

- forward-deployed engineering — drop the swarm onto a client repo when you arrive, take it with you when you leave.
- self-evolving projects — point Bernstein at its own repo and let it execute the backlog (this codebase is one).
- CI fleets — run a swarm of agents in parallel on PRs, with per-agent credential scoping and signed audit trail.
- air-gapped / regulated deployment — install from a signed wheelhouse, run with `--profile airgap` to deny outbound by default, allow-list specific destinations as needed. See [Air-gap installation](docs/installation/air-gap.md).

## supported agents

Bernstein auto-discovers installed CLI agents. Mix them in the same run. Cheap local models for boilerplate, heavier cloud models for architecture.

44 CLI agent adapters: 41 third-party wrappers, 2 leaf-node delegators (Composio, Ralphex), plus a generic wrapper for anything with `--prompt`.

| Agent | Models | Install |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Copilot-managed (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Cursor app](https://www.cursor.com) |
| [Devin Terminal](https://devin.ai) (Cognition) | Devin-managed | `curl -fsSL https://cli.devin.ai/install.sh \| bash` then `devin auth login` |
| [Aider](https://aider.chat) | Any OpenAI/Anthropic-compatible | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Amp-managed | `npm install -g @sourcegraph/amp` |
| [CLM gateway](docs/adapters/clm.md) (sovereign / on-prem LLM) | Any OpenAI-compatible CLM endpoint | `pip install aider-chat`, then set `CLM_ENDPOINT` / `CLM_TOKEN` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph-hosted | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Any OpenAI/Anthropic-compatible | `npm install -g @continuedev/cli` (binary: `cn`) |
| [Goose](https://block.github.io/goose/) | Any provider Goose supports | See [Goose docs](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Any provider the base agent uses | Built-in |
| [Junie](https://junie.jetbrains.com) | BYOK (Anthropic, OpenAI, Google, xAI, OpenRouter, Copilot) | `curl -fsSL https://junie.jetbrains.com/install.sh \| bash` |
| [Kilo](https://kilo.dev) | Kilo-hosted | See [Kilo docs](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Kiro-hosted | See [Kiro docs](https://kiro.dev) |
| [AWS Q Developer](https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line.html) | Amazon Q-managed (Claude-backed) | `brew install --cask amazon-q` then `q login` |
| [Ollama](https://ollama.ai) + Aider | Local models (offline) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Any provider OpenCode supports | See [OpenCode docs](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Qwen Code models | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Workers AI models | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Any LiteLLM-supported (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Any (LiteLLM-backed) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud or self-hosted models | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Letta-routed (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | Any CLI with `--prompt` | Built-in |

#### orchestrator delegation (leaf-node)

A separate, smaller class of adapters that wrap **other CLI orchestrators** as if they were single agents. Bernstein hands the wrapped tool a prompt or plan and only sees the final exit code; sub-agent costs and quality gates inside the wrapped orchestrator are not visible to Bernstein. Useful when you want to drop an existing workflow built on one of these tools into a step of a larger Bernstein plan.

| Orchestrator | Wrapped as | Install |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

Any adapter also works as the **internal scheduler LLM**. Run the entire stack without any specific provider:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Run `bernstein --headless` for CI pipelines. No TUI, structured JSON output, non-zero exit on failure.

## quick start

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

For multi-stage projects, define a YAML plan:

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## how it works

Bernstein runs a four-stage pipeline per goal:

1. **Decompose**. The manager breaks your goal into tasks with roles, owned files, and completion signals. One LLM call, then plain Python from there.
2. **Spawn**. Agents start in isolated [git worktrees](https://git-scm.com/docs/git-worktree), one per task. Main branch stays clean.
3. **Verify**. The janitor checks concrete signals: tests pass, files exist, lint clean, types correct.
4. **Merge**. Verified work lands in main. Failed tasks get retried or routed to a different model.

The orchestrator is a Python scheduler, not an LLM. Scheduling decisions are deterministic, auditable, and reproducible. Every step writes a record to the HMAC-chained audit log (`.sdd/audit/YYYY-MM-DD.jsonl`) per [RFC 2104](https://datatracker.ietf.org/doc/html/rfc2104) — see [docs/security/audit-log.md](docs/security/audit-log.md).

## cloud execution (Cloudflare)

Bernstein can run agents on Cloudflare Workers instead of locally. The `bernstein cloud` CLI handles deployment and lifecycle.

- **Workers**. Agent execution on Cloudflare's edge, with Durable Workflows for multi-step tasks and automatic retry.
- **V8 sandbox isolation**. Each agent runs in its own isolate, no container overhead.
- **R2 workspace sync**. Local worktree state syncs to R2 object storage so cloud agents see the same files.
- **Workers AI** (experimental). Use Cloudflare-hosted models as the LLM provider, no external API keys required.
- **D1 analytics**. Task metrics and cost data stored in D1 for querying.
- **Browser rendering**. Headless Chrome on Workers for agents that need to inspect web output.
- **MCP remote transport**. Expose or consume MCP servers over Cloudflare's network.

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## capabilities

**Core orchestration**. Parallel execution, git worktree isolation, janitor verification, quality gates (lint, types, PII scan), cross-model code review, circuit breaker for misbehaving agents, token growth monitoring with auto-intervention.

**Intelligence**. Contextual bandit router for model/effort selection. Knowledge graph for codebase impact analysis. Semantic caching saves tokens on repeated patterns. Cost anomaly detection (burn-rate alerts). Behavior anomaly detection with Z-score flagging.

**Sandboxing**. Pluggable [`SandboxBackend`](docs/architecture/sandbox.md) protocol; run agents in local git worktrees (default), Docker containers, [E2B](https://e2b.dev) Firecracker microVMs, or [Modal](https://modal.com) serverless containers (with optional GPU). Plugin authors can register custom backends through the `bernstein.sandbox_backends` entry-point group. Inspect installed backends with `bernstein agents sandbox-backends`.

**Artifact storage**. `.sdd/` state can stream to pluggable [`ArtifactSink`](docs/architecture/storage.md) backends: local filesystem (default), S3, Google Cloud Storage, Azure Blob, or Cloudflare R2. `BufferedSink` keeps the WAL crash-safety contract by writing locally with fsync first and mirroring to the remote asynchronously.

**Skill packs**. Progressive-disclosure [skills](docs/architecture/skills.md) (OpenAI Agents SDK pattern): only a compact skill index ships in every spawn's system prompt, agents pull full bodies via the `load_skill` MCP tool on demand. 17 built-in role packs plus third-party `bernstein.skill_sources` entry-points.

**Controls**. [HMAC-SHA256 audit chain](docs/security/audit-log.md) (RFC 2104), policy engine, [lethal-trifecta capability gate](docs/security/lethal-trifecta.md) (refuses spawns whose tool chain combines private data + untrusted input + external comm — Simon Willison's framing, June 2025: *"if your AI agent combines all three of these, an attacker can trick it into stealing your data"*), PII output gating, WAL-backed crash recovery (experimental, multi-worker safety), OAuth 2.0 with PKCE ([RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)) and [RFC 8707](https://datatracker.ietf.org/doc/html/rfc8707) resource-indicator binding, [per-artefact lineage with customer-key Ed25519 signing](docs/compliance/lineage-export.md) ([RFC 8037](https://datatracker.ietf.org/doc/html/rfc8037)) and regulator export.

**Observability**. Prometheus `/metrics`, OTel exporter presets, Grafana dashboards. Per-model cost tracking (`bernstein cost`) plus a [run savings summary](docs/operations/cost-optimization.md#run-savings-summary) on every `bernstein run`. Terminal TUI and web dashboard. Agent process visibility in `ps`.

**Ecosystem**. MCP server mode, A2A protocol support, GitHub App integration, pluggy-based plugin system, multi-repo workspaces, cluster mode for distributed execution, self-evolution via `--evolve` (experimental).

Full feature matrix: [FEATURE_MATRIX.md](docs/reference/FEATURE_MATRIX.md) &middot; Recent features: [What's New](docs/whats-new.md)

### regulatory anchors (as of 2026-05-09)

For compliance reviewers asking "which regulation does Bernstein actually map to":

| Regulation | Mapping | Bernstein surface |
|---|---|---|
| EU AI Act Article 12 (logging) | Automatic record-keeping for high-risk AI systems | `bernstein audit export --article-12 --since … --until …` → deterministic, retention-pinned bundle with audit slice + governance catalog. See [docs/compliance/](docs/compliance/). |
| SOC 2 Trust Service Criteria | CC4 / CC7 (audit + monitoring) | `bernstein audit pack --soc2` → per-control evidence checklist with sha-256 pointers. |
| DORA / NIS2 | Per-artefact lineage with customer-key Ed25519 signature | `bernstein lineage export <run_id> --format jsonld` → schema v2 records. |
| OWASP Agent Security Initiative (ASI06 — memory poisoning, 2026) | Memory provenance audit | `bernstein verify --memory-audit` walks the lesson-memory chain. |
| RFC 2104 (HMAC) | Audit chain integrity | `.sdd/audit/*.jsonl` HMAC-SHA256 with secret outside the audit volume. |
| RFC 7515 §A.5 (detached JWS) + RFC 8785 (JCS) + RFC 8037 (EdDSA) | Signed agent cards + lineage signatures | `src/bernstein/core/security/agent_card_signer.py`, `src/bernstein/core/security/lineage_kms.py`. |
| RFC 7636 (PKCE) + RFC 8707 (resource indicators) | Web dashboard auth + MCP audience binding | `src/bernstein/core/security/oauth_pkce.py`, `auth.py`. |

These are mappings, not certifications. Production accreditation (SOC 2 Type II, ISO 27001) is out of scope for a solo-maintained OSS project; the surfaces exist to make a customer's accreditation path shorter.

## recent releases

**ACP bridge**. `bernstein acp serve --stdio` exposes Bernstein to any editor that speaks the Agent Communication Protocol (Zed, etc.). No plugin code needed on the editor side.

**Autonomous CI repair**. `bernstein autofix` watches open Bernstein PRs and, when CI turns red, spawns a fixer agent automatically. Once green, it pushes the fix and re-requests review.

**Credential vault**. `bernstein connect <provider>` writes API keys to the OS keychain; `bernstein creds` lists and rotates them. Agents inherit scoped credentials without touching environment variables.

**Preview tunnels**. `bernstein preview start` boots a sandboxed dev server and prints a public URL. Useful for sharing a running branch with a reviewer without deploying to staging.

Full changelog: [docs/whats-new.md](docs/whats-new.md)

## operator commands

Commands that eliminate the glue code most teams end up writing around their runs.

| Command | What it does |
|---------|--------------|
| `bernstein pr` | Auto-creates a GitHub PR from a completed session; body carries the janitor's gate results and token/USD cost breakdown. |
| `bernstein from-ticket <url>` | Imports a Linear / GitHub Issues / Jira ticket as a Bernstein task. Label-based role + scope inference. Supports `--dry-run` and `--run`. |
| `bernstein ticket import <url>` | Alias / group form of `from-ticket` for scripting. |
| `bernstein remote` | SSH sandbox backend. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. ControlMaster socket reuse for fast repeat calls. |
| `bernstein hooks` | Lifecycle hooks for `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn`; shell scripts or pluggy `@hookimpl`s. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Drive runs from chat with `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Interactive mid-run tool-call approval. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | One wrapper around four tunnel providers. Also `tunnel list`, `tunnel stop <name>\|--all`. ControlMaster-style process reuse. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Installs a systemd (Linux) or launchd (macOS) unit for auto-start. Also `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Stores and rotates API credentials in the OS keychain. Agents inherit scoped keys per-run. |
| `bernstein autofix` | Daemon that monitors open Bernstein PRs; spawns a fixer agent when CI fails and pushes the repair automatically. |
| `bernstein preview start` | Starts a sandboxed dev server for the current branch and prints a shareable public tunnel URL. |
| `bernstein agents-md` | Generates a canonical [AAIF AGENTS.md](https://agents.md) for the repo and rewrites it into each CLI's native shape. `generate` (preview), `write` (single file), `sync` (canonical + Cursor `.cursor/rules/*.mdc` + Claude `CLAUDE.md` + Aider `CONVENTIONS.md` + Goose `.goosehints`), `verify` (CI gate), `diff` (shows drift between canonical IR and on-disk files). |
| `bernstein scaffold "<prompt>"` | Bootstraps a project skeleton from a single goal prompt. `--template auto\|python-cli\|...`, `--output <dir>`, `--force`. |
| `bernstein wiki build` | Renders `WIKI.md` for the current repo from the AST symbol graph. Local, no LLM call, no cloud round-trip. |
| `bernstein identity show` / `decode` / `verify` / `disable` | Operator-side helpers for the install-rev fingerprint embedded in shared yaml/trace/role-prompt artefacts. No network egress; discovery uses public `gh search code`. |
| `bernstein security role-adapter-policy` | Inspects and edits the per-role adapter allow-list (deny-list enforcement at spawn time). |

### retrieval & caching: what's actually under the hood

Bernstein deliberately uses **no neural embeddings, no vector databases, and no
external embedding APIs**. There are two retrieval/caching layers, both
keyword/lexical:

- **Codebase RAG** (`core/knowledge/rag.py`); [SQLite FTS5](https://sqlite.org/fts5.html)
  with [BM25](https://en.wikipedia.org/wiki/Okapi_BM25) ranking
  and AST-aware chunking for Python files. Built incrementally on file mtime;
  used to enrich agent task context within token budgets.
- **Semantic cache** (`core/knowledge/semantic_cache.py`); despite the name,
  fuzzy matching is done with TF (term-frequency) cosine similarity over word
  counts, not learned embeddings. It deduplicates near-identical LLM planning
  and agent-output requests so we don't re-spawn agents for the same goal.

If you need real semantic retrieval (vector DB, neural embeddings), wire it
yourself via the retrieval role/skill in `templates/`; nothing in core
performs vector search.

## detailed comparison

The closest competitors share Bernstein's shape — multi-agent CLI orchestration in git worktrees. Those are compared in the next table.

The table immediately below covers **previous-generation Python multi-agent frameworks** (CrewAI, AutoGen, LangGraph). Their orchestrator is an LLM that drives Python tool calls; Bernstein's orchestrator is plain Python that drives terminal coding agents. Different problem, different shape — kept here for completeness.

| Feature | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| Orchestrator | Deterministic code | LLM-driven (+ code Flows) | LLM-driven | Graph + LLM |
| Works with | Any CLI agent (44 adapters) | Python SDK classes | Python agents | LangChain nodes |
| Git isolation | Worktrees per agent | No | No | No |
| Pluggable sandboxes | Worktree, Docker, E2B, Modal | No | No | No |
| Verification | Janitor + quality gates | Guardrails + Pydantic output | Termination conditions | Conditional edges |
| Cost tracking | Built-in | `usage_metrics` | `RequestUsage` | Via LangSmith |
| State model | File-based (.sdd/) | In-memory + SQLite checkpoint | In-memory | Checkpointer |
| Remote artifact sinks | S3, GCS, Azure Blob, R2 | No | No | No |
| Self-evolution | Built-in (experimental) | No | No | No |
| Declarative plans (YAML) | Yes | Yes (`agents.yaml`, `tasks.yaml`) | No | Partial (`langgraph.json`) |
| Model routing per task | Yes | Per-agent LLM | Per-agent `model_client` | Per-node (manual) |
| MCP support | Yes (client + server) | Yes | Yes (client + workbench) | Yes (client + server) |
| Agent-to-agent chat | Bulletin board | Yes (Crew process) | Yes (group chat) | Yes (supervisor, swarm) |
| Web UI | TUI + web dashboard | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Cloud hosted option | Yes (Cloudflare) | Yes (CrewAI AMP) | No | Yes (LangGraph Cloud) |
| Built-in RAG/retrieval | Yes (codebase FTS5 + BM25) | `crewai_tools` | `autogen_ext` retrievers | Via LangChain |

*Last verified: 2026-04-19. See [full comparison pages](docs/compare/README.md) for detailed feature matrices.*

The table above compares Bernstein against LLM-orchestration frameworks (they orchestrate LLM calls). The table below covers the closer category: other tools that orchestrate **CLI coding agents**:

| Feature | Bernstein | [claude-flow](https://github.com/ruvnet/claude-flow) | [Archon](https://github.com/coleam00/Archon) | [vibe-kanban](https://github.com/BloopAI/vibe-kanban) | [claude-squad](https://github.com/smtg-ai/claude-squad) | [Composio AO](https://github.com/ComposioHQ/agent-orchestrator) |
|---------|-----------|-----------|-----------|-----------|-----------|-----------|
| Stars (2026-05-13) | 338 | 49k | 21k | 26k | 7.4k | 7k |
| Their hook | regulated / on-prem / audit | swarm intelligence + 314 MCP tools | deterministic + repeatable, web UI | kanban board UI | polished Go TUI, tmux-native | TypeScript dashboard, CI fixer |
| Shape | Python CLI + library + MCP server | CLI + web | CLI + web | desktop board | Go TUI | TypeScript CLI + dashboard |
| Primary language | Python | TypeScript / Node | Python | TypeScript | Go | TypeScript |
| Install | `pipx install bernstein` | `npm install -g claude-flow` | self-host | `npm install -g vibe-kanban` | `brew install claude-squad` | `npm install -g @aoagents/ao` |
| Agent adapters | 44 | ~5 | ~10 | ~6 | ~5 (Claude family) | 3 (Claude Code, Codex, Aider) |
| Parallel multi-agent execution | Yes | Yes (swarm) | Yes | Yes | Yes (tmux multiplex) | Yes |
| Git worktree per agent | Yes | No (swarm-based) | Yes | Yes | Yes | Yes |
| Coordinator | Deterministic Python scheduler | LLM swarm | Workflow + partial LLM | LLM-driven | Plan executor (no LLM in loop) | LLM-driven |
| HMAC-chained audit replay | Yes | No | No | No | No | No |
| Signed agent cards (detached JWS) | Yes | No | No | No | No | No |
| Per-artefact lineage (customer-key signed) | Yes | No | No | No | No | No |
| Air-gap / on-prem profile | Yes (`--profile airgap`) | No (cloud-leaning) | Partial | No | No | No |
| MCP server mode (exposes self as MCP) | Yes (stdio + HTTP/SSE) | Yes (314 tools) | Yes | No | No | No |
| Python library (importable) | Yes | No | No | No | No | No |
| Autonomous CI-fix / PR flow | Yes (`bernstein autofix`) | No | No | No | No | Yes |
| License | Apache 2.0 | MIT | MIT | Apache 2.0 | MIT | MIT |

Star counts and capability snapshots captured 2026-05-12. Earlier-generation CLI-orchestrator competitors (awslabs/cli-agent-orchestrator, emdash, umputun/ralphex) are in [`docs/competitors.md`](docs/competitors.md) with the same matrix.

Bernstein's wedge in this category is the auditability column — HMAC-chained audit, signed agent cards, per-artefact lineage, air-gap profile — plus Python-library shape and the widest adapter coverage. None of the bigger projects has the audit-chain stack; that's the column the regulated-buyer cares about. We are not winning on stars or polish. If you want the polished Go TUI for parallel Claude on your Mac, claude-squad is the right tool. If you want the swarm framing with the broadest MCP tool surface, claude-flow is. If you want a kanban board UI, vibe-kanban is. If you want the workflow-YAML primitive with web UI and chat integration, Archon is. If you need to ship a regulator-ready audit export and run on-prem behind a firewall, Bernstein.

## what people use it for

These are real workflow patterns from Bernstein's own docs, examples, and project surface, not invented customer quotes.

- **Parallel test generation**. Fan out across untested modules with `BERNSTEIN_MAX_AGENTS=5 bernstein -g "Generate unit tests for untested modules in src/"`.
- **CI failure repair**. Watch open PRs and dispatch scoped fixers with `bernstein autofix start --repo your-org/your-repo --foreground`.
- **PR review follow-up**. Turn review comments into tracked fix tasks with `bernstein review-responder start --repo your-org/your-repo --foreground`.
- **Codebase modernization**. Run wide refactors like `BERNSTEIN_MAX_AGENTS=8 bernstein -g "Migrate callback-based modules in src/ to async/await and update tests"`.
- **Ticket-to-run workflows**. Import GitHub, Jira, or Linear work directly with `bernstein from-ticket https://github.com/your-org/your-repo/issues/123 --run`.
- **API-change safety checks**. Catch downstream breakage before merge with `bernstein dep-impact --base main`.

See [Who Uses Bernstein](docs/use-cases.md) for the longer version with command examples and notes on when each workflow fits.

[^autogen]: AutoGen is in maintenance mode; successor is Microsoft Agent Framework 1.0.

## monitoring

```bash
bernstein live       # TUI dashboard
bernstein dashboard  # web dashboard
bernstein status     # task summary
bernstein ps         # running agents
bernstein cost       # spend by model/task
bernstein doctor     # pre-flight checks
bernstein recap      # post-run summary
bernstein export     # shareable HTML/Markdown report of the latest run
bernstein trace <ID> # agent decision trace
bernstein run-changelog --hours 48  # changelog from agent-produced diffs
bernstein explain <cmd>  # detailed help with examples
bernstein dry-run    # preview tasks without executing
bernstein dep-impact # API breakage + downstream caller impact
bernstein aliases    # show command shortcuts
bernstein config-path    # show config file locations
bernstein init-wizard    # interactive project setup
bernstein debug-bundle   # collect logs, config, and state for bug reports
bernstein skills list    # discoverable skill packs (progressive disclosure)
bernstein skills show <name>  # print a skill body with its references
```

```bash
bernstein fingerprint build --corpus-dir ~/oss-corpus  # build local similarity index
bernstein fingerprint check src/foo.py                 # check generated code against the index
```

## install

| Method | Command |
|--------|---------|
| **One-liner (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **One-liner (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (wrapper) | `npx bernstein-orchestrator` |
| **Docker (GHCR)** | `docker run --rm -v "$PWD:/work" -w /work -e ANTHROPIC_API_KEY ghcr.io/sipyourdrink-ltd/bernstein:latest run -g "fix tests/test_foo.py"` |

The one-liner scripts check for Python 3.12+, bootstrap pipx when it's missing, fix PATH for the current session, and install (or upgrade) `bernstein`. They handle brew-managed macOS environments and the Windows `py -3` launcher fallback. Script sources: [install.sh](scripts/install.sh) · [install.ps1](scripts/install.ps1).

### optional extras

Provider SDKs are optional so the base install stays lean. Pick what you need:

| Extra | Enables |
|-------|---------|
| `bernstein[openai]` | OpenAI Agents SDK v2 adapter (`openai_agents`) |
| `bernstein[docker]` | Docker sandbox backend |
| `bernstein[e2b]` | [E2B](https://e2b.dev) microVM sandbox backend (needs `E2B_API_KEY`) |
| `bernstein[modal]` | [Modal](https://modal.com) sandbox backend, optional GPU (needs `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | S3 artifact sink (via `boto3`) |
| `bernstein[gcs]` | Google Cloud Storage artifact sink |
| `bernstein[azure]` | Azure Blob artifact sink |
| `bernstein[r2]` | Cloudflare R2 artifact sink (S3-compatible `boto3`) |
| `bernstein[grpc]` | gRPC bridge |
| `bernstein[k8s]` | Kubernetes integrations |

Combine extras with brackets, e.g. `pip install 'bernstein[openai,docker,s3]'`.

Editor extensions: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## "powered by bernstein" badge (optional)

If your project ships diffs that bernstein helped land, you can advertise it:

```markdown
[![signed by bernstein](https://img.shields.io/badge/signed_by-bernstein-FBBF24?logo=githubactions&logoColor=white&style=flat-square)](https://bernstein.run/?utm_source=badge&utm_medium=readme&utm_campaign=powered-by)
```

`bernstein init --add-badge` injects it into your README under the existing badge stack. Variants: `signed`, `audited-by`, `orchestrated-by`, `crew-managed-by` — pass via `--badge-variant`. Picky maintainers can keep their READMEs untouched: the flag is opt-in.

## contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and code style.

## support

If Bernstein saves you time: [GitHub Sponsors](https://github.com/sponsors/chernistry)

Contact: [forte@bernstein.run](mailto:forte@bernstein.run)

## featured in

Curated lists, newsletters, and peer projects that picked up Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (April 23, 2026); newsletter mention.
- [**Future Digest**](https://futuredigestnews.substack.com/p/your-claude-bill-just-hit-874-heres) (April 30, 2026); Bernstein cited as the self-host orchestrator for long-running autonomous sessions in a cost-cutting playbook.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators); editorial roundup; "the most architecturally interesting tool in this roundup."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md); Bernstein cited as the production implementation of the "deterministic zero-LLM orchestration" pattern.
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**punkpeye/awesome-mcp-servers**](https://github.com/punkpeye/awesome-mcp-servers); flagship MCP-server directory.
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix); Nix flake distribution.

<details>
<summary>More awesome lists & community curation</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [taishi-i/awesome-ChatGPT-repositories](https://github.com/taishi-i/awesome-ChatGPT-repositories) (日本語 + EN)
- [eudk/awesome-ai-tools](https://github.com/eudk/awesome-ai-tools)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein); editorial MCP server listing.
- Mirrors: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Cited as prior art by peer projects</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md); long-form bakeoff treating Bernstein as the reference implementation.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework); `BERNSTEIN_PATTERNS.md`, "Patterns Worth Borrowing".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench); research notes on the manager/janitor split.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md); comparison article positioning Bernstein on the deterministic end.

</details>

## cite

If Bernstein helps your research or industry work, please cite it. Machine-readable metadata lives in [CITATION.cff](CITATION.cff) (CFF 1.2.0); GitHub renders the "Cite this repository" button automatically. A Zenodo DOI will be minted on the next release once Zenodo's GitHub integration is enabled — see [CITATION.cff](CITATION.cff) for the current canonical citation.

## license

[Apache License 2.0](LICENSE)

---

Made with love by [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

## translations

[Español](docs/i18n/README.es.md) &middot; [中文](docs/i18n/README.zh.md) &middot; [العربية](docs/i18n/README.ar.md) &middot; [Português](docs/i18n/README.pt.md) &middot; [Bahasa Indonesia](docs/i18n/README.id.md) &middot; [Français](docs/i18n/README.fr.md) &middot; [日本語](docs/i18n/README.ja.md) &middot; [Русский](docs/i18n/README.ru.md) &middot; [Deutsch](docs/i18n/README.de.md) &middot; [עברית](docs/i18n/README.he.md) &middot; [יידיש](docs/i18n/README.yi.md)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
