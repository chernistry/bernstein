# Changelog

All notable project changes are tracked here (code + docs).

## Unreleased

### Changed — chat bridge

- **Telegram driver simplified to a single long-poll path.** The `python-telegram-bot` v22 long-poll driver at `bernstein.core.chat.drivers.telegram` is the only Telegram driver. Configure a bot API token from `@BotFather` and a chat id; no external services. The earlier optional bridge-router architecture has been removed.
- **Telegram notification sink simplified.** `TelegramSink` accepts a live `TelegramBridge` via `config["bridge"]` or a token string via `config["token"]` and routes through the standard long-poll path.

## [2.0.0] — 2026-05-15

### Added — operator GUI

- **Web GUI ships as `pip install bernstein[gui]` extras.** Five operator screens (Tasks / Agents / Approvals / Audit / Costs) served by `bernstein gui serve`. The historical TUI / dashboard surfaces remain available; the GUI is an additive surface for operators who prefer a browser.
- **`bernstein run --idle` mode for GUI development without LLM token spend.** Forces the mock adapter with sleep 15-120s heartbeats so the operator UI can be exercised end-to-end against a populated `.sdd/` runtime without burning real provider quota.
- **`bernstein_playground/` recipe for zero-cost orchestrator demos.** Pre-baked plan + mock-adapter wiring lets a fresh clone show the GUI populated with realistic tasks/agents/approvals in under one minute.

### Changed — operator surface

- **Sidebar information architecture reduced 8 → 5 items.** Settings collapsed into the user menu; Fleet promoted to a topbar toggle; Overview folded into Tasks. The flat top-level list is now scannable on a 13" laptop without horizontal scroll.
- **Default theme follows system preference (was hardcoded dark).** `prefers-color-scheme` is honoured on first load; the operator override (light/dark/system) persists in localStorage.

### Internal

- **New `src/bernstein/gui/` package.** FastAPI router modules feed the GUI screens off the existing `.sdd/` runtime; no new persistence layer.
- **Vite 6 + React 18 + Tailwind 3 + shadcn/ui scaffold under `web/`.** Built artefacts ship inside the wheel under the `[gui]` extra; no Node toolchain required at install time.

## [1.10.1] — 2026-05-07

### Added — adapters

- **Devin for Terminal (Cognition).** First-class adapter with 558 lines of contract tests covering process tracking, env isolation, and timeout watchdogs. Drop-in for any plan via `cli_agent: devin_terminal`.
- **JetBrains Junie CLI.** LLM-agnostic BYOK adapter (`cli_agent: junie`) — forwards whichever provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) the routed model needs and dynamically narrows the network allowlist to that provider's endpoints.
- **AWS Q Developer CLI.** First-class adapter (`cli_agent: q_dev`) using `q chat --no-interactive --trust-all-tools`. Token bootstrap via `q login` is documented in the adapter docstring; missing token cache surfaces a clear error rather than a silent hang. IAM Identity Center role inheritance noted as a deployment risk.
- **Cursor adapter rewrite.** Replaced shell to non-existent `cursor agent` binary with the real `cursor-agent` CLI surface (`-p --workspace --output-format stream-json --trust --approve-mcps --force`); 242 lines of new contract tests.

### Added — operator surfaces

- **Run savings summary.** Each `bernstein run` summary card now reports estimated savings vs running the same plan single-shot through the most expensive routed model.

### Fixed

- **Handoff tokens prefixed with `h_`.** `secrets.token_urlsafe()` produces a `-`-leading token in roughly 1.5% of issuances; click misparses `bernstein handoff claim TOKEN` as if `-V` were an option. Fix issues all tokens with the `h_` prefix.

### Documentation

- **Enterprise evaluation guide** — deployment shapes Bernstein already supports (laptop tool, on-prem cluster, air-gap-clean wheelhouse, MCP server mode behind a corporate egress proxy) and the audit, lineage, and operator surfaces to interrogate before bringing it inside a regulated perimeter.
- **Use-case workflows page** (`docs/use-cases.md`) — four most-asked patterns: continuous codebase audit, stale-PR triage, parallel adapter benchmarking, post-mortem evidence pack. Contributed by @zerone0x via #1048.
- Internal scheduler-LLM example bumped from `gemini-2.5-pro` to `gemini-3.1-pro`.
- Author identity surfaces (sameAs / rel=me / twitter:creator) reconciled across bernstein.run, alexchernysh.com, and the SoftwareApplication JSON-LD on the docs site.

### Tooling

- README's CodeTrendy banner shrunk from a 104px image strip to an inline shields.io badge.
- `--max-agents` doc references replaced with the real `BERNSTEIN_MAX_AGENTS` env var (the public surface since 1.8).

## [1.10.0] — 2026-05-05

### Added — operator surface

- **Cluster-mode hardening** — native mTLS for node-to-node transport with `bernstein cluster bootstrap-ca`; real 2-process e2e test harness with 6 chaos scenarios (worker crash, central restart, network partition, token expiry, concurrent claims); 5 Prometheus metrics + 6 audit event types; documented Cloudflare Tunnel + Tailscale deployment patterns with nightly CI smoke.
- **Air-gap distribution** — `scripts/build_airgap_wheelhouse.py` resolves the pinned dep closure into a signed wheelhouse; `bernstein verify <wheelhouse>` checksum + signature verification (cosign default, GPG path); new `--profile airgap` egress gate denies adapter/MCP network calls outside an explicit allow-list; `bernstein doctor airgap` self-checks.
- **Per-artifact lineage trail** — every agent file write emits a signed record linking output (path + byte range + sha) to inputs, producer, prompt SHA, model, cost, tokens; schema v2 adds `regulatory_class` + customer-key Ed25519 signature for DORA/NIS2 evidence; tamper-loud detection in janitor with SIEM webhook + `bernstein lineage verify <run_id>`.
- **Lethal-trifecta capability matrix** — declarative tags (PRIVATE_DATA / UNTRUSTED_INPUT / EXTERNAL_COMM); spawn-time refusal of any agent whose tool chain unions all three; bypass-immune via `policy_engine.evaluate_lethal_trifecta`; phase-emit policies now ride the same matrix.

### Added — orchestration depth

- **CLM (Cyber Language Model) gateway adapter** — thin sovereign-LLM adapter wrapping `aider` against an OpenAI-compatible CLM gateway; tool-calling allowlist, streaming-assembly lineage, opt-in mTLS via Phase 2.5 launcher shim.
- **Phase pipeline** — discrete research/plan/implement/verify phase separation with distilled JSON handoffs; per-phase JSON-Schema validation registered as capability-matrix policy; R001-R005 mechanical exit gates (no-open-questions, decisions-reference-prior, acyclic graph, monotonic constraints, byte budget) with re-fire on violation; gate results land in lineage trail.
- **Action cache** — `core/persistence/action_cache.py` layered on the new `MemoStore` for deterministic replay; `bernstein cache action stats|replay <run_id>`.
- **Fingerprint memoization** — `hash(args) + hash(fn-AST)` keys; applied to cross-model verifier, knowledge-graph extractor, RAG embedder; the `test_changed_function_body_changes_key` regression closes the silent-stale-cache bug.
- **Rework-rate ledger** — file-backed `(model, effort, phase, outcome)` JSONL under `.sdd/runtime/rework/`; cascade router auto-promotes (e.g. `sonnet → opus`) once the bucket exceeds `promotion_threshold=0.30` with `min_samples=20`.
- **Best-of-N delegation** — opt-in parallel candidate spawning with judge-based selection; new `BEST_OF_N` defaults section; per-task `Task.best_of_n=K` override.
- **Swarm migration** — `bernstein migrate` map-reduce fanout over file globs; idempotent via `.sdd/runtime/swarm/<plan>.json`; 2 starter migration templates.
- **Discrete phase pipeline** — opt-in via `defaults.PHASE_PIPELINE.enabled` and per-step `phases:` field in plan YAML.

### Added — quality + planning

- **AST-aware reviewer chunking** — Python reviewer never receives a chunk that splits a function or class.
- **Abstracted code review** — intent + pseudocode summary on diffs; cheap-tier reviewer with opus disallowed; collapsible raw-diff blocks in PR body.
- **Schema-validation retry** — cross-step error accumulation with `SchemaRetryContext`; wired into manager parsing + MCP tool result decoding.
- **Spec-as-test loop** — generates executable assertions from the immutable feature contract; gates on drift.
- **Feature contract** — `.sdd/contract/features.json` with anchor over immutable fields + HMAC chain anchor; tampering surfaces `TamperingDetectedError`.
- **Incident-to-eval synthesis** — terminally-failed tasks become regression eval cases under `eval/incident_synthesizer.py`.

### Added — protocols + integrations

- **Tool-search lazy loading** — meta-tool with BM25 ranking keeps MCP tool descriptions out of context until invoked.
- **Static service manifest** — `/.well-known/agent.json` (A2A-compliant) + `/llms.txt` from a single dataclass-driven endpoint table.
- **Spawner SandboxSession routing** — non-worktree backends now exec through `SandboxSession.exec()` with per-session asyncio loop; worktree backend stays on the legacy direct-subprocess path.
- **Session handoff** — `bernstein handoff emit|claim|status`; `/handoff` chat slash-command + dashboard route; ring buffer for stream-tail replay.
- **Routine-scenario bridge** — bidirectional `RoutineProvisioner` + 8 scenario templates; `bernstein routine scenarios|export|provision|register|bindings`.
- **Agent-mode profiles** — declarative `templates/mode_profiles/{smart,deep,fast}.yaml`; deterministic family mapping (sonnet/opus → smart, haiku/qwen/ollama → fast, gpt-5*/o-series → deep).
- **cocoindex-code MCP catalog entry** — registered as opt-in (`mcp.catalog.cocoindex_code.enabled = false` by default).

### Changed

- **Model catalogue refresh** — added GPT-5.5 / GPT-5.5-mini to cost + cascade tables; refreshed top-7 adapter install commands (claude, codex, gemini, ollama, cursor, aider, opencode); `Last verified 2026-05-05` markers on every adapter docstring.
- **Default branch** — direct push to `main` is the convention everywhere; documentation + scripts updated to never reference `master`.

### Documentation

- Full doc audit covering every feature shipped this release; new pages under `docs/concepts/`, `docs/cluster/`, `docs/observability/`, `docs/compliance/`, `docs/sandbox/`, `docs/installation/`, `docs/adapters/`. Every feature page covers: one-line description, why, how-to, configuration knobs, limitations, related.

## [1.7.0] — 2026-04-14

### Added
- **Cloudflare integration platform** (twelve modules):
  - Workers RuntimeBridge (`bridges/cloudflare.py`) — agent execution on Workers + Durable Objects
  - Workflow Bridge (`bridges/cloudflare_workflow.py`) — durable multi-step workflows with auto-retry and approval gates
  - Sandbox Bridge (`bridges/cloudflare_sandbox.py`) — V8 isolate and container sandboxes for isolated code execution
  - Browser Rendering Bridge (`bridges/browser_rendering.py`) — headless web browsing, screenshots, scraping, PDF generation
  - R2 Workspace Sync (`bridges/r2_sync.py`) — content-addressed delta file sync via Cloudflare R2
  - Workers AI Provider (`core/routing/cloudflare_ai.py`) — free-tier LLM models (Llama 3.1, Mistral, Gemma, Qwen) for planning
  - D1 Analytics Client (`core/cost/d1_analytics.py`) — usage metering, billing tiers (free/pro/team/enterprise), quota enforcement
  - MCP Remote Transport (`mcp/remote_transport.py`) — streamable HTTP transport for remote MCP server access
  - Cloud CLI (`cli/commands/cloud_cmd.py`) — `bernstein cloud` subcommands: login, logout, run, status, runs, cost, deploy
  - Cloudflare Agents Adapter (`adapters/cloudflare_agents.py`) — spawn agents via `npx wrangler dev`
  - Codex-on-Cloudflare Adapter (`adapters/codex_cloudflare.py`) — run Codex in Cloudflare sandboxes
- Full Cloudflare documentation: overview, setup, bridges, adapters, Workers AI, analytics, CLI, MCP remote (8 new doc pages)

## [1.4.11] — 2026-04-03

### Added
- **Bernstein doctor** — comprehensive pre-flight health check: adapters, API keys, ports, `.sdd/` integrity, MCP servers. Auto-repair mode with `--fix`.
- **Per-agent token progress** — real-time token usage tracking per spawned agent, surfaced in `bernstein status`.
- **Context injection token budget** — explicit budgets for injected context (files, lessons, RAG chunks) with graceful truncation and priority ordering.
- **Output style customization** — configurable agent output format via markdown templates.
- **Installation mismatch detection** — detects gaps between expected and installed adapter capabilities.
- **API preconnect warmup** — connection warmup before heavy runs to reduce first-request latency.
- **Worker badge identity** — process identification visible in `bernstein ps` and Activity Monitor.
- **TUI keybinding system** — configurable keyboard shortcuts in the Textual dashboard.
- **Progressive permission prompts** — per-agent permission levels for fine-grained control.
- **Activity tracking metrics** — session-level activity statistics and agent usage patterns.
- **Away summary generation** — summarize what happened while you were away.
- **Commit attribution stats** — per-agent commit statistics.
- **Session analytics** — cumulative insights across runs.
- **Settings snapshot in traces** — agent settings preserved in execution traces.
- **Side question support** — agents can ask clarifying questions mid-task.
- **Diff folding display** — folded diff rendering in agent output.
- **Word-level diff rendering** — character-level change highlighting.
- **Contextual tips system** — in-context hints for agents.
- **Session tag system** — tag and filter runs.
- **Rename session** — session renaming command.
- **Security review command** — `bernstein security-review` for vulnerability assessment.
- **Cumulative progress tracking** — progress tracking across runs.
- **Plugin trust warning** — warns on unverified plugins.
- **Plugin error reporting** — improved error diagnostics for plugin failures.
- **Extra usage provisioning** — additional usage quota management.
- **Truecolor mode detection** — automatic terminal color capability detection.
- **Dirty flag layout caching** — caching optimizations for dirty project detection.
- **Release notes display** — show release notes on startup.

### Fixed
- Context warnings in `bernstein doctor` output for better diagnostics.
- Circuit breaker for repeated compact failures — prevents agent thrashing.

### Changed
- Documentation overhaul: README, GETTING_STARTED, ARCHITECTURE, FEATURE_MATRIX, BENCHMARKS, CHANGELOG, CONTRIBUTING all rewritten against v1.4.11 codebase.

## [1.4.9] — 2026-04-01

### Added
- Process-aware shutdown/drain improvements across CLI and core lifecycle paths.
- Cost analytics enhancements (additional endpoints/aggregation work and routing transparency updates).
- Security enhancements including sensitivity-classification and IP-allowlist related hardening.
- TUI keyboard help (`?`) shortcut support.

### Changed
- Issue triage and documentation alignment pass so docs match shipped behaviour.
- Retry, lifecycle, and observability narratives updated to better reflect current implementation boundaries.

## [1.4.0] — 2026-03-31

### Added
- **Plan Files**: loadable YAML project plans with stages and steps (`bernstein run plan.yaml`)
- **Server Supervisor**: auto-restart on crash with exponential backoff (max 5 restarts / 10 min)
- **CrashGuard Middleware**: catches unhandled exceptions → 500 instead of process death
- **Orchestrator drain mode**: loop continues while agents are active, even after stop signal
- **Quality gates**: PII scan, mutation testing, benchmark regression detection
- **Gate Runner**: parallel execution of all quality gates (asyncio)
- **Benchmark regression gate**: block merge when performance degrades beyond threshold
- **PII log redaction**: auto-installed filter scrubs emails, phones, SSNs, credit cards from all log output
- **Agent loop detection**: kills agents caught in edit-loop cycles (same file edited N+ times in window)
- **Deadlock detection**: wait-for graph cycle detection with automatic victim selection
- **Cost anomaly detection**: Z-score based cost anomaly signaling with configurable thresholds
- **Per-agent file/command permissions**: role-based matrix restricting which files and commands each role may use
- **Premium visual theme**: CRT power-off effects, gradient splash, block-art logo
- **Live boot log**: orchestrator boot progress shown in Agents panel while no agents spawned
- **Persistent memory**: SQLite-backed cross-session agent memory
- **Context handoff**: structured context briefs for subtask delegation
- **Zero-config mode**: auto-detect project type, no bernstein.yaml required
- **Worktree environment hooks**: auto-symlink node_modules, copy .env
- **FIFO merge queue**: sequential merge with git merge-tree conflict pre-check
- **Ticket Format v1**: YAML frontmatter with model routing, janitor signals, tags
- **10 adapters**: Claude, Codex, Cursor, Gemini, Kiro, OpenCode, Aider, Amp, Roo Code, Generic
- **Futuristic splash screen**: full-screen animated boot sequence
- **Plan display**: mission-briefing style execution plan approval
- **test_cli_run_params.py**: catches cli() → run() parameter sync bugs

### Fixed
- Manager always uses opus/max (was falling back to haiku via fast_path)
- Orchestrator no longer exits while agents still running
- Server failure backoff: 5s per failure instead of constant polling
- Startup crash: missing pii_scan fields in QualityGatesConfig
- .yaml/.md backward compatibility in all backlog parsers

### Changed
- Ticket format migrated from .md to .yaml (YAML frontmatter)
- Version bump 1.3.x → 1.4.0

## [1.0.3] — 2026-03-30

### Added
- State-of-the-art CI/CD pipeline: 11 new GitHub Actions workflows
- Three-tier AI PR review (GitHub Models + Gemini CLI + Bernstein deep review)
- Semgrep SAST, license compliance, spelling, dead code analysis, workflow linting
- PR auto-labeling, size warnings, stale cleanup, Dependabot auto-merge
- Release Drafter for automated changelog generation
- Telegram bot notifications on CI completion
- Codecov coverage gating (85% project / 70% patch)
- Concurrency groups on all workflows with cancel-in-progress
- CI and Codecov badges in README

### Changed
- FEATURE_MATRIX updated with CI/CD section (15 new entries)
- GETTING_STARTED expanded with CI pipeline documentation
- Manual backlog index updated with all setup tickets and status tracking

## [1.0.2] — 2026-03-28

### Changed
- Documentation audit: updated outdated model names, CLI references, API endpoints, and GitHub Action version tags
- Default branch references updated from `master` to `main` across all docs

## [1.0.0] — 2026-03-28

### Added
- ACP (Agent Communication Protocol) endpoints for agent interoperability
- A2A (Agent-to-Agent) protocol support
- Cluster mode with multi-node coordination (node registration, heartbeat, status)
- Auth routes: OIDC, SAML, CLI device flow, group mappings, user management
- Graduation system for agent promotion based on performance
- Plans routes for plan listing, approval, and rejection
- Slack integration (slash commands and events)
- Quality dashboard with per-model quality metrics
- Cost history, live cost tracking, and cost alerts endpoints
- File lock tracking via dashboard routes
- Task prioritization, force-claim, and progress reporting endpoints
- Chaos testing CLI group
- Audit CLI group
- Verify CLI command

### Changed
- Version bumped to 1.0.0 (stable release)
- Route modules expanded: acp.py, auth.py, graduation.py, plans.py, slack.py added to core/routes/

## [0.3.0] — 2026-03-28

### Added
- Checkpoint and wrap-up CLI commands for session management
- Task snapshots endpoint for viewing task state history
- Webhook alerts endpoint
- SSE event stream at `/events` for real-time dashboard updates
- Prometheus `/metrics` endpoint for observability
- Bandit-based model routing stats at `/routing/bandit`
- Cache stats endpoint at `/cache-stats`

### Changed
- CLI decomposed further: audit_cmd.py, chaos_cmd.py, checkpoint_cmd.py, verify_cmd.py, wrap_up_cmd.py
- Task server routes expanded with block, progress, and prioritize actions

## [0.2.0] — 2026-03-28

### Added
- Agent discovery system with multi-provider routing (`cli: auto`)
- Quality gates for task verification
- Rule enforcement engine
- Token monitor for real-time usage tracking
- Approval gates for high-risk operations
- MCP server integration
- Hot reload for configuration changes
- Aider, Amp, and Roo Code adapters
- Adapter manager and caching adapter layer
- Environment isolation for adapter processes
- Web dashboard with real-time SSE updates
- Workspace management for multi-repo orchestration
- GitHub App integration for webhook-driven tasks
- Auth middleware and checkpoint commands
- Delegate, trigger, and wrap-up CLI commands

### Changed
- Default CLI adapter is now `auto` (detects installed agents) instead of `claude`
- Test count badge updated: 2500+ to 4250+ (142 test files, 4257 test functions)
- Server decomposed into `core/routes/` (tasks.py, status.py, webhooks.py, costs.py, agents.py, auth.py, dashboard.py, plans.py, quality.py, graduation.py, slack.py)
- Orchestrator decomposed into tick_pipeline.py, task_lifecycle.py, agent_lifecycle.py
- CLI decomposed into helpers.py, run_cmd.py, stop_cmd.py, status_cmd.py, agents_cmd.py, evolve_cmd.py, advanced_cmd.py, and more
- TaskStore extracted to task_store.py with PostgreSQL and Redis backends
- `bernstein catalog` commands renamed to `bernstein agents` (sync, list, validate)
- Adapter listing in DESIGN.md updated to include all current adapters (removed stale kiro.py)
- Example YAML files updated: `cli: claude` changed to `cli: auto`
- All documentation references to `bernstein catalog` updated to `bernstein agents`
- Removed stale "(default)" label from Claude adapter docs (default is now `auto`)

## [0.1.0] — 2026-03-28

### Added
- License: Apache 2.0
- Per-run cost budgeting (`--budget 5.00`) with threshold warnings
- CI auto-fix pipeline with GitHub Actions log parser
- GitHub Action (`action.yml`) for CI-triggered orchestration
- MCP tool access — agents use MCP servers (stdio/SSE)
- TUI session manager (`bernstein live`) with Textual
- "The Bernstein Way" architecture tenets document
- Quickstart demo (`examples/quickstart/`)
- Comparison pages (`docs/compare/`)
- GitHub Action documentation (`docs/github-action.md`)
- Feature cards for cost budgeting, GitHub Action, MCP on index page
- `docs/competitive-matrix.md` — feature comparison vs CrewAI, AutoGen, LangGraph, etc.
- `docs/zero-lock-in.md` — model-agnostic architecture deep dive
- `docs/CHANGELOG.md` — this file
- `docs/VERSION` — documentation version tracking

### Changed
- All license references updated to Apache 2.0 across all HTML and markdown docs
- README: quickstart section with full install → init → run flow
- README: test count badge, license badge, benchmark badge
- Getting Started: fixed test command to use isolated runner
- Comparison table: added cost budgeting and GitHub Action rows
