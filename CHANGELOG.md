# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

Release notes for tagged versions are also generated automatically by
[release-drafter](https://github.com/release-drafter/release-drafter) and
published on the [GitHub Releases page](https://github.com/chernistry/bernstein/releases).
This file captures the human-curated highlights.

## [Unreleased]

## [1.9.0] - 2026-04-19

### Added

- **OpenAI Agents SDK v2 adapter (`openai_agents`)** — ticket oai-001. New
  first-class CLI adapter that wraps `agents.Agent` + `Runner.run_sync` in a
  subprocess so the existing Bernstein spawner can manage lifecycle,
  timeouts, rate-limit back-off, and cost tracking. Structured JSONL event
  stream, MCP bridging through the runner manifest (Bernstein-managed MCP
  servers are forwarded into `RunConfig`), pricing rows for `gpt-5`,
  `gpt-5-mini`, and `o4`. Install with `pip install 'bernstein[openai]'`.
- **Pluggable sandbox backends** — ticket oai-002. A new
  `SandboxBackend` / `SandboxSession` protocol lets every spawned agent
  run inside a local git worktree (default), a Docker container, an E2B
  Firecracker microVM, or a Modal serverless container (with optional GPU).
  Third parties register custom backends via the
  `bernstein.sandbox_backends` entry-point group. `plan.yaml` gains an
  optional per-stage `sandbox:` block. `bernstein agents sandbox-backends`
  lists every installed backend with its capability set. Extras:
  `bernstein[docker]`, `bernstein[e2b]`, `bernstein[modal]`.
- **Cloud artifact storage sinks** — ticket oai-003. New async
  `ArtifactSink` protocol decouples `.sdd/` persistence from the local
  filesystem. First-party sinks: `local_fs` (default), `s3`, `gcs`,
  `azure_blob`, `r2`. `BufferedSink` preserves the WAL crash-safety
  contract by fsyncing locally first and mirroring to the remote
  asynchronously. Third parties register sinks via the
  `bernstein.storage_sinks` entry-point group. Extras: `bernstein[s3]`,
  `bernstein[gcs]`, `bernstein[azure]`, `bernstein[r2]`.
- **Progressive-disclosure skill packs** — ticket oai-004. Role prompts
  migrated to the OpenAI Agents SDK "Skills" shape: only a compact skill
  index ships in every spawn's system prompt; agents pull full bodies via
  the `load_skill` MCP tool on demand. 17 built-in role packs (backend,
  qa, security, frontend, devops, architect, docs, retrieval,
  ml-engineer, reviewer, manager, vp, prompt-engineer, visionary,
  analyst, resolver, ci-fixer). New CLI: `bernstein skills list` /
  `bernstein skills show <name> [--reference … | --script …]`. Plugin
  authors register additional skill sources through
  `bernstein.skill_sources`. Legacy `templates/roles/` path still loads
  as a fallback for two more minor versions.
- Honest 3-line terminal transcript in README hero area alongside the GIF.
- New architecture pages: `docs/architecture/sandbox.md`,
  `docs/architecture/storage.md`, `docs/architecture/skills.md`.
- New user-facing summary page: `docs/whats-new.md`.

### Changed

- **Adapter count updated to 18** (17 third-party wrappers + generic). New
  row for `openai_agents` in README, `CONTRIBUTING.md`, `ADAPTER_GUIDE.md`,
  `compatibility.md`, `GETTING_STARTED.md`, and every comparison page.
- Comparison pages' "Last verified" stamp bumped from 2026-04-17 to
  2026-04-19. Compare tables now call out pluggable sandbox backends and
  remote artifact sinks as Bernstein-side differentiators.
- README model column dropped stale patch versions: Claude uses `Opus 4`,
  `Sonnet 4.6`, `Haiku 4.5`; Codex uses `GPT-5` / `GPT-5 mini`; Gemini
  uses `Gemini 2.5 Pro` / `Gemini Flash`.
- README install one-liner now uses `pipx install bernstein` and runs
  `bernstein init` before `bernstein -g`. New "Optional extras" table
  documents `bernstein[openai,docker,e2b,modal,s3,gcs,azure,r2,grpc,k8s]`.
- Softened README claims per backlog findings: "zero LLM tokens on
  scheduling" to "no LLM calls in selection, retry, or reap decisions";
  dropped "tamper-evident" from audit logs, "no silent data loss" from
  WAL recovery, "learns optimal ... over time" from bandit router,
  "Z-score flagging" from cost anomaly detection, "pluggy-based" from
  plugin system. Marked Workers AI and `--evolve` as experimental.
- README badge row trimmed to CI, PyPI, Python 3.12+, and License.
- `docs/architecture/ARCHITECTURE.md` gains a "Sandbox, storage, and
  skills (1.9.x)" cross-link section and fixes the `What to read next`
  relative paths that pointed at non-existent sibling files.
- `mkdocs.yml` nav exposes the three new architecture pages under
  *Concepts*, the OpenAI Agents adapter guide under *Guides*, the
  `openai_agents vs codex / claude / gemini` decision page under
  *Comparisons*, and *What's New* under *Reference*.

### Removed

- README rows for Roo Code, Tabby, and Codex on Cloudflare (not a
  `CLIAdapter`). `ADAPTER_GUIDE.md` and `compatibility.md` lose their
  matching stale rows.
- Dead `opencollective.com/bernstein` link from the Support section.
- Over-claim of "36 CLI adapters" in `docs/compare/openai-agents.md`;
  reality is 18.

## [1.7.0] - 2026-04-13

### Changed
- **Major architecture refactoring**: reorganized `core/` from 533 flat files into 22 sub-packages
  (orchestration/, agents/, tasks/, quality/, server/, cost/, tokens/, security/, config/,
  observability/, protocols/, git/, persistence/, planning/, routing/, communication/,
  knowledge/, plugins_core/, routes/, memory/, trigger_sources/, grpc_gen/).
- Module decomposition: `orchestrator.py` split into 7+ sub-modules, `spawner` into 4,
  `task_lifecycle` into 4, with backward-compatible shims at the original import paths.
- Created `defaults.py` with 150+ configurable constants extracted from scattered literals.
- CLI commands reorganized into `cli/commands/` sub-package (70+ command modules).

### Added
- `bernstein debug-bundle` command for collecting logs, config, and state for bug reports.
- IaC (Infrastructure-as-Code) adapter.
- 2,600+ new tests (total test files now exceed 1,000).
- Protocol negotiation for MCP/A2A compatibility.
- Quality gates, cost tracking, and token monitoring moved into dedicated sub-packages.

### Fixed
- Numerous orchestration, lifecycle, and merge-ordering bugs addressed during refactoring.

## [1.6.4] - 2026-04-11

### Fixed
- Orchestration: serialize merges via lock; remove dangerous pre-merge rebase.
- Spawner: close path-traversal and log-injection in retry path.
- File locks: add threading lock to `FileLockManager`; protect approval gate.
- Agents: reap agents before fetch; protect verify loop; FIFO eviction.
- Completion flow reordered — merge before close, cleanup after PR.
- 20 critical orchestration bugs covering merge serialization, gate ordering,
  completion flow, and agent lifecycle.
- GitHub sync skips issues that already have an assignee.
- CI: mutation testing score parser correctness.
- Activity-summary poller debounced flaky timing assertion.

## [1.6.0] - 2026-04
### Added
- CLI command aliases wired through the main entry point.

## [1.5.0] - 2026-03
### Added
- Multi-repo workspace commands and cluster mode improvements.

## [1.4.0] - 2026-02
### Added
- Knowledge graph for codebase impact analysis.
- Semantic caching to reduce token spend on repeated patterns.
- Cost anomaly detection with Z-score flagging.

## [1.3.0] - 2026-01
### Added
- Cross-model code review.
- HMAC-chained tamper-evident audit logs.
- WAL-based crash recovery.

## [1.2.0] - 2025-12
### Added
- Quality gates: lint + types + PII scan pipeline.
- Token growth monitoring with auto-intervention.

## [1.1.0] - 2025-11
### Added
- Janitor verification of concrete completion signals.
- Circuit breaker for misbehaving agents.

## [1.0.0] - 2025-10
### Added
- Initial public release.
- Deterministic Python orchestrator with file-based state in `.sdd/`.
- Adapters for Claude Code, Codex CLI, Gemini CLI, Cursor, Aider, and a generic
  `--prompt` adapter.
- YAML plan execution (`bernstein run plan.yaml`).
- TUI dashboard, web dashboard, Prometheus `/metrics`, and OTel exporter
  presets.

[Unreleased]: https://github.com/chernistry/bernstein/compare/v1.9.0...HEAD
[1.9.0]: https://github.com/chernistry/bernstein/compare/v1.7.0...v1.9.0
[1.7.0]: https://github.com/chernistry/bernstein/compare/v1.6.4...v1.7.0
[1.6.4]: https://github.com/chernistry/bernstein/compare/v1.6.0...v1.6.4
[1.6.0]: https://github.com/chernistry/bernstein/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/chernistry/bernstein/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/chernistry/bernstein/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/chernistry/bernstein/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/chernistry/bernstein/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/chernistry/bernstein/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/chernistry/bernstein/releases/tag/v1.0.0
