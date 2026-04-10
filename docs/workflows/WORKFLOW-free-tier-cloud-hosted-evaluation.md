# WORKFLOW: Free-Tier Cloud-Hosted Bernstein for Evaluation
**Version**: 0.1
**Date**: 2026-04-11
**Author**: Workflow Architect
**Status**: Draft
**Implements**: road-009 — Free-tier cloud-hosted Bernstein for evaluation (no install required)

---

## Overview

A publicly accessible web endpoint where prospects paste a GitHub URL, select a solution pack, and watch a sandboxed Bernstein instance orchestrate up to 3 agents working on their repo. Eliminates the "I don't have time to install it" objection by offering a zero-install evaluation experience. Hard-capped at $2 compute budget per session, public repos only, ephemeral sandbox destroyed after completion or timeout.

---

## Actors

| Actor | Role in this workflow |
|---|---|
| Prospect | Unauthenticated visitor who submits a GitHub URL and watches the demo |
| Web frontend | SPA that collects input, streams progress, displays agent activity |
| API gateway | Rate-limits, validates requests, enforces abuse protections |
| Session manager | Creates and tracks evaluation sessions with TTL and budget |
| Repo validator | Validates GitHub URL, confirms public access, checks size/safety limits |
| Sandbox provisioner | Creates an isolated filesystem + Bernstein instance per session |
| Bernstein orchestrator (sandboxed) | Standard orchestrator running inside the sandbox with capped config |
| Agent processes (max 3) | CLI agents spawned by the sandboxed orchestrator |
| Budget monitor | Watches token/cost accumulation, triggers hard stop at $2 |
| Progress streamer | SSE/WebSocket bridge that relays orchestrator state to the web frontend |
| Cleanup service | Destroys sandbox resources after session ends or times out |

---

## Prerequisites

- Cloud compute environment with container/VM provisioning capability (e.g., Fly.io, Railway, AWS ECS, or similar)
- Bernstein Docker image published to a container registry
- Web frontend deployed and publicly accessible
- API gateway with rate-limiting and abuse-detection middleware
- GitHub API access for repo metadata validation (public, unauthenticated)
- Solution pack definitions stored in a configuration registry
- Cost tracking integrated with provider APIs or token-counting adapters
- DNS + TLS configured for the public endpoint

---

## Trigger

**User action**: Prospect visits the evaluation page, pastes a GitHub repo URL, selects a solution pack, and clicks "Start".

**Exact entry point**: `POST /api/v1/eval/sessions`

---

## Solution Pack Definitions

Solution packs are pre-configured task templates that define what the agents will do. Each pack maps to a set of Bernstein roles and task descriptions.

```yaml
# solution-packs.yaml
packs:
  - id: code-review
    name: "Code Review"
    description: "3 agents review your codebase for bugs, security issues, and code quality"
    roles: [reviewer, security, qa]
    max_agents: 3
    estimated_minutes: 10
    tasks:
      - role: reviewer
        title: "Code quality review"
        scope: small
      - role: security
        title: "Security audit"
        scope: small
      - role: qa
        title: "Test coverage analysis"
        scope: small

  - id: bug-triage
    name: "Bug Triage"
    description: "Agents analyze open issues and suggest fixes"
    roles: [resolver, analyst]
    max_agents: 2
    estimated_minutes: 15
    tasks:
      - role: analyst
        title: "Issue analysis and prioritization"
        scope: medium
      - role: resolver
        title: "Suggest fixes for top issues"
        scope: medium

  - id: docs-gen
    name: "Documentation Generation"
    description: "Generate missing documentation for your codebase"
    roles: [docs, analyst]
    max_agents: 2
    estimated_minutes: 10
    tasks:
      - role: analyst
        title: "Identify undocumented public APIs"
        scope: small
      - role: docs
        title: "Generate documentation for identified gaps"
        scope: medium
```

---

## Workflow Tree

### STEP 1: Validate request and rate-limit
**Actor**: API gateway
**Action**: Check request origin, enforce per-IP rate limits, validate request payload schema
**Timeout**: 2s
**Input**: `{ "github_url": "string", "pack_id": "string", "email": "string | null" }`
**Output on SUCCESS**: validated payload -> GO TO STEP 2
**Output on FAILURE**:
  - `FAILURE(rate_limited)`: IP has exceeded 3 sessions/hour -> return 429 + `{ "error": "Too many evaluation requests. Try again in {retry_after_s}s.", "code": "RATE_LIMITED", "retryable": true }`
  - `FAILURE(invalid_payload)`: Missing or malformed fields -> return 400 + `{ "error": "Invalid request: {details}", "code": "INVALID_PAYLOAD", "retryable": false }`
  - `FAILURE(blocked_origin)`: Known abuse IP/fingerprint -> return 403 + `{ "error": "Request blocked.", "code": "BLOCKED", "retryable": false }`

**Observable states during this step**:
  - Prospect sees: submit button spinner
  - Operator sees: request in API gateway access logs
  - Database: nothing persisted yet
  - Logs: `[api-gateway] eval request ip={ip} url={github_url} pack={pack_id}`

---

### STEP 2: Validate GitHub repository
**Actor**: Repo validator
**Action**: Fetch repo metadata via GitHub API (`GET https://api.github.com/repos/{owner}/{repo}`). Confirm: (a) repo exists, (b) repo is public, (c) repo size < 500 MB, (d) repo is not archived, (e) repo is not empty, (f) repo is not a fork-of-fork (depth > 2). Fetch default branch name for clone.
**Timeout**: 10s
**Input**: `{ "github_url": "string" }` (parsed to owner/repo)
**Output on SUCCESS**: `{ "owner": "string", "repo": "string", "default_branch": "string", "size_kb": int, "language": "string | null" }` -> GO TO STEP 3
**Output on FAILURE**:
  - `FAILURE(not_found)`: Repo does not exist -> return 404 + `{ "error": "Repository not found. Only public repositories are supported.", "code": "REPO_NOT_FOUND", "retryable": false }`
  - `FAILURE(private_repo)`: Repo is private -> return 403 + `{ "error": "Private repositories are not supported in the free evaluation. Install Bernstein locally for private repos.", "code": "REPO_PRIVATE", "retryable": false }`
  - `FAILURE(too_large)`: Repo > 500 MB -> return 422 + `{ "error": "Repository is too large for evaluation (max 500 MB).", "code": "REPO_TOO_LARGE", "retryable": false }`
  - `FAILURE(archived)`: Repo is archived -> return 422 + `{ "error": "Archived repositories cannot be evaluated.", "code": "REPO_ARCHIVED", "retryable": false }`
  - `FAILURE(empty)`: Repo has no commits -> return 422 + `{ "error": "Repository is empty.", "code": "REPO_EMPTY", "retryable": false }`
  - `FAILURE(github_api_timeout)`: GitHub API did not respond in 10s -> return 502 + `{ "error": "Could not reach GitHub. Try again.", "code": "GITHUB_TIMEOUT", "retryable": true }`
  - `FAILURE(github_api_rate_limit)`: GitHub API rate limit hit -> return 502 + `{ "error": "GitHub API rate limit reached. Try again in a few minutes.", "code": "GITHUB_RATE_LIMITED", "retryable": true }`

**Observable states during this step**:
  - Prospect sees: "Validating repository..."
  - Operator sees: GitHub API call in service logs
  - Database: nothing persisted yet
  - Logs: `[repo-validator] validating repo={owner}/{repo} size={size_kb}KB`

---

### STEP 3: Create evaluation session
**Actor**: Session manager
**Action**: Generate a unique session ID. Persist session record with status=`provisioning`, TTL=30 minutes, budget_remaining_usd=2.00, max_agents=3. If email was provided, associate it for follow-up. Check concurrent session count (global max: configurable, e.g., 50 concurrent sessions).
**Timeout**: 3s
**Input**: `{ "owner": "string", "repo": "string", "default_branch": "string", "pack_id": "string", "email": "string | null", "client_ip": "string" }`
**Output on SUCCESS**: `{ "session_id": "string", "stream_url": "string", "dashboard_url": "string" }` -> RETURN 201 to prospect, GO TO STEP 4
**Output on FAILURE**:
  - `FAILURE(capacity_full)`: Concurrent session limit reached -> return 503 + `{ "error": "All evaluation slots are in use. Try again in a few minutes.", "code": "CAPACITY_FULL", "retryable": true }`
  - `FAILURE(db_error)`: Session store unavailable -> return 500 + `{ "error": "Internal error. Try again.", "code": "SESSION_STORE_ERROR", "retryable": true }`

**Observable states during this step**:
  - Prospect sees: redirect to dashboard page with session_id; "Provisioning sandbox..." spinner
  - Operator sees: session record in eval_sessions table with status=`provisioning`
  - Database: `eval_sessions(id={session_id}, status='provisioning', budget_remaining=2.00, pack_id=..., repo=..., created_at=now())`
  - Logs: `[session-mgr] session created id={session_id} repo={owner}/{repo} pack={pack_id}`

---

### STEP 4: Provision sandbox
**Actor**: Sandbox provisioner
**Action**: (a) Clone the repo into an isolated filesystem (shallow clone, depth=1, default branch only). (b) Create a sandboxed Bernstein instance with capped configuration: max_agents=3, budget_usd=2.00, timeout_minutes=20, no network egress except to provider APIs, read-only access to repo (agents work on a copy). (c) Write `.sdd/config.yaml` with sandbox constraints. (d) Start the Bernstein task server on a randomly assigned internal port. (e) Health-check the server.
**Timeout**: 120s (clone can be slow for large repos)
**Input**: `{ "session_id": "string", "owner": "string", "repo": "string", "default_branch": "string", "pack_id": "string", "budget_usd": 2.00, "max_agents": 3 }`
**Output on SUCCESS**: `{ "sandbox_id": "string", "server_port": int, "workdir": "string", "clone_size_bytes": int }` -> GO TO STEP 5
**Output on FAILURE**:
  - `FAILURE(clone_failed)`: Git clone failed (network, auth, corrupt) -> `{ "error": "Failed to clone repository.", "code": "CLONE_FAILED", "retryable": true }` -> RETRY x1 after 10s -> ABORT_CLEANUP
  - `FAILURE(clone_timeout)`: Clone exceeded 120s -> ABORT_CLEANUP
  - `FAILURE(sandbox_boot_failed)`: Bernstein server did not pass health check -> ABORT_CLEANUP
  - `FAILURE(disk_quota_exceeded)`: Clone exceeds sandbox disk quota -> `{ "error": "Repository exceeds sandbox disk limits.", "code": "DISK_QUOTA", "retryable": false }` -> ABORT_CLEANUP

**Observable states during this step**:
  - Prospect sees: "Cloning repository..." then "Starting sandbox..."
  - Operator sees: sandbox container/VM spinning up, session status=`provisioning`
  - Database: `eval_sessions.status='provisioning'`, `eval_sandboxes(id={sandbox_id}, session_id=..., status='starting')`
  - Logs: `[sandbox] provisioning sandbox={sandbox_id} repo={owner}/{repo} clone_depth=1`

---

### STEP 5: Inject solution pack tasks
**Actor**: Session manager + sandboxed orchestrator
**Action**: (a) Read solution pack definition for pack_id. (b) Create tasks on the sandboxed task server via `POST /tasks` for each task in the pack. (c) Configure the sandboxed orchestrator's model routing to use the most cost-effective models (haiku/sonnet cascade). (d) Start the orchestrator tick loop.
**Timeout**: 10s
**Input**: `{ "sandbox_id": "string", "server_port": int, "pack_id": "string" }`
**Output on SUCCESS**: `{ "task_ids": ["string", ...], "agent_count": int }` -> GO TO STEP 6
**Output on FAILURE**:
  - `FAILURE(task_creation_failed)`: Task server rejected tasks -> ABORT_CLEANUP
  - `FAILURE(invalid_pack)`: Pack ID not found in registry -> ABORT_CLEANUP
  - `FAILURE(orchestrator_start_failed)`: Orchestrator failed to start tick loop -> ABORT_CLEANUP

**Observable states during this step**:
  - Prospect sees: "Configuring agents..." then task list appears on dashboard
  - Operator sees: tasks created in sandbox task store
  - Database: `eval_sessions.status='running'`, `eval_sessions.task_ids=[...]`
  - Logs: `[session-mgr] injected {n} tasks for pack={pack_id} session={session_id}`

---

### STEP 6: Agent execution with live streaming
**Actor**: Sandboxed orchestrator + agent processes + progress streamer
**Action**: The sandboxed orchestrator runs its normal tick loop: claim tasks, spawn agents, monitor heartbeats, process completions. The progress streamer reads the sandbox's task server state and SSE endpoint, translating internal events into prospect-friendly updates pushed via WebSocket/SSE to the frontend. The budget monitor polls cost accumulation every 10s.
**Timeout**: 20 minutes (hard session TTL)
**Input**: orchestrator running autonomously
**Output on SUCCESS**: all tasks reach DONE/CLOSED status -> GO TO STEP 7
**Output on FAILURE**:
  - `FAILURE(budget_exhausted)`: Cost accumulation hit $2.00 -> budget monitor sends SHUTDOWN signal to all agents -> GO TO STEP 7 (partial results)
  - `FAILURE(session_timeout)`: 20-minute TTL expired -> SHUTDOWN signal -> GO TO STEP 7 (partial results)
  - `FAILURE(all_agents_failed)`: Every agent failed (no retries left) -> GO TO STEP 7 (failure results)
  - `FAILURE(orchestrator_crashed)`: Sandboxed orchestrator exited unexpectedly -> ABORT_CLEANUP

**Streaming events (prospect-facing)**:
```json
{ "type": "agent_spawned", "agent_id": "string", "role": "string", "task_title": "string" }
{ "type": "agent_progress", "agent_id": "string", "progress_pct": 0-100, "message": "string" }
{ "type": "agent_completed", "agent_id": "string", "result_summary": "string", "files_changed": ["string"] }
{ "type": "agent_failed", "agent_id": "string", "error": "string" }
{ "type": "budget_update", "spent_usd": 0.00, "remaining_usd": 2.00 }
{ "type": "session_complete", "status": "success|partial|failed", "summary": "string" }
```

**Observable states during this step**:
  - Prospect sees: live dashboard with agent cards showing role, progress bar, current activity, files changed, cost ticker
  - Operator sees: active sandbox with agent processes, cost accumulation, heartbeat status
  - Database: `eval_sessions.status='running'`, `eval_sessions.cost_usd` incrementing, per-task status updates
  - Logs: `[sandbox:{session_id}] agent={agent_id} role={role} progress={pct}% cost=${cost}`

---

### STEP 7: Generate evaluation report
**Actor**: Session manager
**Action**: Collect results from all completed (and failed) tasks. Generate a summary report including: (a) what each agent found/produced, (b) files that would be changed (as diffs), (c) total cost, (d) total time. If email was provided, queue a follow-up email with the report link. Mark session as `completed`.
**Timeout**: 15s
**Input**: `{ "session_id": "string", "task_results": [{ "task_id": "string", "status": "string", "result_summary": "string", "files_changed": ["string"] }] }`
**Output on SUCCESS**: `{ "report_url": "string", "report": { ... } }` -> GO TO STEP 8
**Output on FAILURE**:
  - `FAILURE(report_generation_failed)`: Summary generation failed -> still GO TO STEP 8 (cleanup must happen regardless), mark report as unavailable

**Observable states during this step**:
  - Prospect sees: "Generating report..." then full results page with diffs, summaries, cost breakdown
  - Operator sees: session status=`completed`, report artifact stored
  - Database: `eval_sessions.status='completed'`, `eval_reports(session_id=..., report_json=...)`
  - Logs: `[session-mgr] report generated session={session_id} tasks_completed={n} cost=${total}`

---

### STEP 8: Schedule sandbox cleanup
**Actor**: Cleanup service
**Action**: (a) Stop all agent processes in the sandbox (SIGTERM, 10s grace, SIGKILL). (b) Stop the sandboxed orchestrator and task server. (c) Schedule filesystem/container destruction after a grace period (15 minutes — allows prospect to review results). (d) After grace period: destroy the sandbox filesystem, reclaim compute resources, remove the cloned repo. (e) Mark session as `cleaned_up`.
**Timeout**: 30s for stop signals; 15 minutes grace before destroy
**Input**: `{ "session_id": "string", "sandbox_id": "string" }`
**Output on SUCCESS**: sandbox destroyed, resources reclaimed
**Output on FAILURE**:
  - `FAILURE(orphaned_sandbox)`: Cleanup partially failed, resources leaked -> alert operator, add to orphan cleanup queue

**Observable states during this step**:
  - Prospect sees: results page remains accessible for 15 minutes; after that, a cached summary page
  - Operator sees: session status transitions `completed` -> `draining` -> `cleaned_up`
  - Database: `eval_sessions.status='cleaned_up'`, `eval_sessions.cleaned_at=now()`
  - Logs: `[cleanup] sandbox={sandbox_id} session={session_id} destroyed`

---

### ABORT_CLEANUP: Session Failure Cleanup
**Triggered by**: STEP 4 failures, STEP 5 failures, STEP 6 orchestrator crash
**Actions** (in order):
  1. Send SHUTDOWN signal to all agent processes in the sandbox (if any were spawned)
  2. Stop the sandboxed orchestrator and task server (if running)
  3. Destroy the sandbox filesystem and container/VM
  4. Set `eval_sessions.status='failed'`, `eval_sessions.error='{error_code}: {message}'`
  5. Emit cleanup metric: `eval_session_aborted{reason="{error_code}"}`
  6. If sandbox destruction fails, add to orphan cleanup queue and alert operator
**What prospect sees**: "Something went wrong. Please try again." + retry button. If email was provided, no follow-up is sent for failed sessions.
**What operator sees**: session in `failed` state with error details, orphan alerts if cleanup failed.

---

## State Transitions

```
[created] -> (step 3: session persisted) -> [provisioning]
[provisioning] -> (step 4: sandbox ready) -> [running]
[running] -> (step 6: all tasks done or budget/timeout hit) -> [completed]
[completed] -> (step 7: report generated) -> [draining]
[draining] -> (step 8: grace period + destroy) -> [cleaned_up]

Failure paths:
[provisioning] -> (step 4 failure) -> [failed] -> [cleaned_up]
[running] -> (orchestrator crash) -> [failed] -> [cleaned_up]
[failed] -> (cleanup fails) -> [failed + orphan_alert]
```

---

## Handoff Contracts

### Web Frontend -> API Gateway
**Endpoint**: `POST /api/v1/eval/sessions`
**Payload**:
```json
{
  "github_url": "string — full GitHub repo URL (https://github.com/owner/repo)",
  "pack_id": "string — solution pack identifier (code-review, bug-triage, docs-gen)",
  "email": "string | null — optional email for follow-up report"
}
```
**Success response (201)**:
```json
{
  "session_id": "string",
  "stream_url": "string — WebSocket/SSE URL for live progress",
  "dashboard_url": "string — URL to view the evaluation dashboard"
}
```
**Failure response**:
```json
{
  "ok": false,
  "error": "string — human-readable error message",
  "code": "string — machine-readable error code",
  "retryable": true
}
```

### Web Frontend -> Progress Streamer
**Endpoint**: `GET /api/v1/eval/sessions/{session_id}/stream` (SSE) or `wss://.../eval/sessions/{session_id}/ws` (WebSocket)
**Authentication**: Session ID is the bearer (no login required; session ID is unguessable UUID)
**Message format**: Newline-delimited JSON events (see STEP 6 streaming events)
**Timeout**: Connection held open for session duration (max 20 min)
**On disconnect**: Client reconnects with `Last-Event-ID` header for resumption

### Session Manager -> Sandbox Provisioner
**Endpoint**: Internal RPC / container orchestration API
**Payload**:
```json
{
  "session_id": "string",
  "repo_clone_url": "string — https://github.com/owner/repo.git",
  "default_branch": "string",
  "pack_id": "string",
  "budget_usd": 2.00,
  "max_agents": 3,
  "timeout_minutes": 20,
  "disk_quota_mb": 1024,
  "network_policy": "egress-to-provider-apis-only"
}
```
**Success response**:
```json
{
  "sandbox_id": "string",
  "server_port": 8052,
  "workdir": "/sandbox/{session_id}/repo",
  "health_check_url": "http://sandbox-{id}:8052/health"
}
```
**Failure response**:
```json
{
  "ok": false,
  "error": "string",
  "code": "PROVISION_FAILED | CLONE_FAILED | DISK_QUOTA | BOOT_FAILED",
  "retryable": true
}
```
**Timeout**: 120s

### Budget Monitor -> Sandboxed Orchestrator
**Mechanism**: Write SHUTDOWN signal file to `.sdd/runtime/signals/*/SHUTDOWN` for each active agent session inside the sandbox. Also POST to sandboxed server's `/stop` endpoint.
**Trigger**: `cost_accumulated_usd >= budget_usd`
**Poll interval**: 10s
**Grace period**: 30s after SHUTDOWN signal before SIGKILL

---

## Cleanup Inventory

| Resource | Created at step | Destroyed by | Destroy method |
|---|---|---|---|
| eval_sessions DB record | Step 3 | Never (audit trail) | Retained; status updated to `cleaned_up` |
| Sandbox container/VM | Step 4 | STEP 8 / ABORT_CLEANUP | Container orchestration API delete |
| Cloned repository filesystem | Step 4 | STEP 8 / ABORT_CLEANUP | `rm -rf` within container destroy |
| Sandboxed Bernstein .sdd/ state | Step 4 | STEP 8 / ABORT_CLEANUP | Destroyed with container |
| Agent processes | Step 6 | STEP 8 / ABORT_CLEANUP | SIGTERM -> SIGKILL (30s grace) |
| Sandboxed task server | Step 4 | STEP 8 / ABORT_CLEANUP | SIGTERM -> container destroy |
| eval_reports artifact | Step 7 | TTL-based expiry | Expire after 30 days |
| WebSocket/SSE connections | Step 6 | STEP 8 | Server-side close on session end |

---

## Abuse Prevention

| Threat | Mitigation |
|---|---|
| Crypto mining via agent commands | Sandbox has no outbound network except provider APIs; CPU/memory capped per container; agent tool allowlists restrict to read-only analysis packs |
| Repo bomb (zip bomb, massive history) | Shallow clone (depth=1); disk quota per sandbox (1 GB); repo size pre-check via GitHub API |
| Session flooding | Per-IP rate limit (3 sessions/hour); global concurrent session cap; CAPTCHA after 2nd session from same IP |
| Cost overrun | Hard $2 budget cap enforced by budget monitor; provider API keys scoped to evaluation account with billing alerts |
| Prompt injection via repo content | Agents run in sandboxed workdir; no access to host secrets; sandbox network policy prevents data exfiltration |
| Persistent resource leak | Orphan cleanup service runs every 5 minutes; any sandbox older than TTL+grace is force-destroyed |

---

## Test Cases

| Test | Trigger | Expected behavior |
|---|---|---|
| TC-01: Happy path | Valid public repo, code-review pack | Session created, 3 agents run, report generated, sandbox cleaned up |
| TC-02: Private repo | Private repo URL | 403 returned immediately at STEP 2, no session created |
| TC-03: Non-existent repo | Invalid GitHub URL | 404 returned at STEP 2, no session created |
| TC-04: Repo too large | Repo > 500 MB | 422 returned at STEP 2, no session created |
| TC-05: Rate limit | 4th session from same IP within 1 hour | 429 returned at STEP 1 |
| TC-06: Capacity full | Global session limit reached | 503 returned at STEP 3 |
| TC-07: Clone failure | GitHub down during clone | Retry x1, then ABORT_CLEANUP, prospect sees error + retry button |
| TC-08: Budget exhaustion | Agents consume $2 before tasks complete | SHUTDOWN signal sent, partial results in report |
| TC-09: Session timeout | Tasks not complete after 20 min | SHUTDOWN signal sent, partial results in report |
| TC-10: All agents fail | Every agent hits a permanent error | Session completes with failure report, sandbox cleaned up |
| TC-11: Orchestrator crash | Sandboxed orchestrator exits unexpectedly | ABORT_CLEANUP, prospect sees error |
| TC-12: Orphaned sandbox | Cleanup fails | Orphan detected by periodic scan, force-destroyed, operator alerted |
| TC-13: Concurrent sessions | Same user starts 2 sessions | Both run independently within rate limits |
| TC-14: Reconnect stream | WebSocket disconnects mid-session | Client reconnects with Last-Event-ID, receives missed events |
| TC-15: Empty repo | Repo with no commits | 422 at STEP 2 |

---

## Assumptions

| # | Assumption | Where verified | Risk if wrong |
|---|---|---|---|
| A1 | GitHub unauthenticated API rate limit (60 req/hr) is sufficient for repo validation | Not verified — depends on traffic volume | Validation fails for prospects; mitigate with GitHub App token for higher limits |
| A2 | Shallow clone (depth=1) is sufficient for all solution packs | Not verified | Some packs (e.g., git history analysis) would need deeper clones; scope packs to avoid this |
| A3 | Haiku/Sonnet cascade stays within $2 for typical solution packs on repos < 500 MB | Not verified — needs benchmarking | Budget exhaustion before useful output; adjust pack scope or budget cap |
| A4 | Container provisioning completes within 120s including clone | Not verified — depends on infra | Prospects abandon slow sessions; pre-warm container pools |
| A5 | Sandbox network policy can allow provider API egress while blocking everything else | Depends on infra platform | Agent processes could exfiltrate data; critical security assumption |
| A6 | Agent tool allowlists prevent destructive operations in analysis packs | Verified: Claude adapter has ROLE_ALLOWED_TOOLS | Low risk — but must audit all adapters, not just Claude |
| A7 | 50 concurrent sessions is feasible on target compute budget | Not verified | Infra cost may exceed free-tier viability; start with 10 and scale |
| A8 | WebSocket/SSE reconnection with Last-Event-ID provides gap-free delivery | Standard SSE spec behavior | Missed events during reconnect; buffer last 100 events server-side |

---

## Open Questions

- **Q1**: What cloud platform will host the sandboxes? This affects container provisioning, network policy enforcement, and cost structure. (Candidates: Fly.io, Railway, AWS ECS Fargate, Google Cloud Run)
- **Q2**: Do solution packs produce actual commits/PRs, or only read-only analysis reports? Write access adds complexity (fork management, PR creation) but increases demo impact.
- **Q3**: Should the evaluation capture lead information (email) as a hard requirement, or is it optional? Marketing may want gating; product may want frictionless access.
- **Q4**: What provider API keys are used in the sandbox? A shared evaluation account, or per-session ephemeral keys? Shared keys are simpler but create a blast radius if compromised.
- **Q5**: Should the evaluation page be behind a waitlist/beta signup, or fully public from day 1? Affects rate limiting strategy and capacity planning.
- **Q6**: How long should evaluation reports persist after the session? 30 days is assumed; longer retention increases storage cost.
- **Q7**: Should the budget monitor track actual provider API costs (requires billing API integration) or estimated costs (token counting)? Estimation is faster but less accurate.

---

## Spec vs Reality Audit Log

| Date | Finding | Action taken |
|---|---|---|
| 2026-04-11 | Initial spec created | — |
| 2026-04-11 | Existing `FreeTierMaximizer` and `TierHijacker` in core handle provider quota tracking — can be reused for budget monitoring within sandboxes | Noted in A3; implementation should integrate with existing cost tracking |
| 2026-04-11 | Existing agent signal protocol (WAKEUP/SHUTDOWN files) is directly usable for budget-triggered shutdown | STEP 6 failure path uses existing signal mechanism |
| 2026-04-11 | No web frontend or API gateway exists in the current codebase — this is entirely new infrastructure | Flagged as major implementation gap |
