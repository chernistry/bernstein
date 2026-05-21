# Autofix

**Audience:** teams adopting Bernstein for self-driving CI repair on
its own pull requests.

**What:** `bernstein autofix` is a long-running daemon that watches a
configured set of GitHub repositories, finds failed CI runs on PRs *that
Bernstein itself opened*, classifies the failure into a routing bucket,
and dispatches a fresh deterministic Bernstein run scoped to the failing
log. The repair commit lands on the same branch; humans don't move.

**Why:** When Bernstein opens 30 PRs/day, transient flakiness, formatter
churn, and dependency lint becomes the long pole. Autofix closes the loop
without you. The classifier is keyword-based and deterministic so every
escalation is auditable. See
`src/bernstein/core/autofix/__init__.py:1-30`.

Cross-link: [Quality pipeline](../architecture/quality-pipeline.md) for
the in-run gate semantics; autofix is the *post-merge* counterpart that
handles failures CI catches after the orchestrator already let go.

---

## What autofix does, end-to-end

Source: `src/bernstein/core/autofix/dispatcher.py:1-34`. Per dispatched
attempt:

1. **Tick** every `poll_interval_seconds` (default 60 s). For each
   configured repo, list open PRs with currently-failing CI runs.
2. **Ownership gate** - read the PR description / commit trailers,
   require a `bernstein-session-id: <id>` line written by `bernstein pr`
   that resolves to a known local session
   (`src/bernstein/core/autofix/ownership.py:1-15`).
3. **Label gate** - require the `bernstein-autofix` label on the PR.
   Removing the label aborts in-flight attempts within one tick.
4. **Cap check** - fail-fast `needs-human` once the PR has burned
   `MAX_ATTEMPTS_PER_PUSH = 3` attempts on the active push SHA
   (`src/bernstein/core/autofix/config.py:60-61`).
5. **Cost check** - `cost_cap_usd` per repo. Default $5. An attempt that
   would breach the cap is aborted and a comment is posted.
6. **Classifier** - keyword sweep over the failing log:
    - `security` (CodeQL, CVE, leaked-secret, dependabot) → `opus`.
    - `flaky`    (timeouts, deadlock, rate-limit, 5xx) → `sonnet`.
    - `config`   (lint, mypy, ruff, missing env, syntax) → `haiku`.

   Highest-priority match wins, so a security signal always beats flaky.
   Source: `src/bernstein/core/autofix/classifier.py:1-18`.
7. **Audit open** - append `autofix.attempt.start` to the HMAC chain.
8. **Goal synthesis** - deterministic short prompt assembled from PR
   metadata + truncated log (`log_byte_budget`, default 64 KiB).
9. **Spawn** - invoke the dispatch hook (production: `bernstein run`
   with the synthesised goal and bandit-selected model). Tests inject a
   stub.
10. **Audit close** - append `autofix.attempt.end` with outcome,
    `commit_sha`, `cost_usd`. Both events share an `attempt_id` so
    `bernstein audit` joins them.

The dispatcher never pushes to git or comments on PRs directly - those
side-effects flow through the `ActionAdapter` protocol so the daemon is
fully testable without the network
(`src/bernstein/core/autofix/dispatcher.py:74-85`).

---

## `bernstein autofix` group

Source: `src/bernstein/cli/commands/autofix_cmd.py`.

### `autofix start`

```console
$ bernstein autofix start [--repo OWNER/REPO ...]
                          [--config /path/to/autofix.toml]
                          [--foreground]
                          [--once]
```

By default the command double-forks the daemon and returns the PID of
the long-running grandchild. Use the systemd / launchd integration
(`bernstein daemon install`) so the OS owns restart logic
(`src/bernstein/cli/commands/autofix_cmd.py:1-13`).

Flags:

- `--repo OWNER/REPO` - restrict the tick to specific repos. Repeatable.
  Unknown repos (not in `autofix.toml`) emit a warning but don't abort.
- `--config <path>` - override the default `autofix.toml` location.
- `--foreground` - stay attached. Use under systemd; never daemonise
  twice.
- `--once` - single tick then exit. Useful for cron-driven setups that
  prefer external scheduling over a long-lived daemon.

### `autofix stop`

```console
$ bernstein autofix stop [--timeout 10]
```

Sends `SIGTERM` to the PID stored in `.sdd/runtime/autofix.pid`, waits
up to `--timeout` seconds for clean exit, then clears the pid file
(`src/bernstein/core/autofix/daemon.py:388-417`). Raises
`DaemonNotRunningError` if no live daemon is found.

### `autofix status`

```console
$ bernstein autofix status [--limit 20] [--json] [--watch]
```

Prints daemon up/down state, last-tick timestamp, and the most recent N
attempts. `--json` emits the full snapshot. `--watch` tails new entries
as they land in the JSONL status log.

```
autofix daemon: running (pid=4711)
last tick:      Wed May  4 14:23:11 2026

Recent attempts (newest first):
  sipyourdrink-ltd/bernstein#1042  attempt=2  outcome=success     classifier=flaky    cost=$0.0314
  sipyourdrink-ltd/bernstein#1041  attempt=1  outcome=needs_human classifier=security cost=$0.0000
```

### `autofix attach`

```console
$ bernstein autofix attach [--limit 200]
```

Replays the last N attempts as JSON-per-line, then tails new entries -
same surface `attach` provides for chat-control sessions
(`src/bernstein/cli/commands/autofix_cmd.py:398-436`). This is the
"resume from any terminal" handoff used by the chat-control surfaces.

---

## Trigger conditions

**Automatic (daemon picks up on tick):**

- PR is open on a watched repo.
- PR has the `bernstein-autofix` label
  (`src/bernstein/core/autofix/config.py:57`).
- PR description / commits contain `bernstein-session-id: <id>` matching
  a local session
  (`src/bernstein/core/autofix/ownership.py:35-40`).
- One or more required check runs failed on the latest push.
- Active push SHA has burned fewer than `MAX_ATTEMPTS_PER_PUSH` (3)
  autofix attempts.
- Repo's `cost_cap_usd` budget would not be breached by the attempt.

**Manual (no autofix):**

- Removing the `bernstein-autofix` label aborts in-flight attempts on
  the next tick.
- After 3 attempts, the daemon adds a `needs-human` label and stops
  retrying that push SHA.
- `security`-classified failures still trigger autofix (with `opus`)
  unless the failure pattern indicates a CVE in a transitive dep where
  the fix requires human judgement; the keyword classifier is a
  heuristic - escalating manually by removing the label is always
  available.

Outcomes recorded on the `outcome` Prometheus label
(`src/bernstein/core/autofix/dispatcher.py:60-66`):

| Outcome       | Meaning                                                        |
|---------------|----------------------------------------------------------------|
| `success`     | Spawn produced a commit; CI is expected to flip green.         |
| `failed`      | Spawn ran but the commit didn't fix CI.                        |
| `cost_capped` | Aborted before dispatch; would have breached `cost_cap_usd`.   |
| `needs_human` | Attempt cap reached; `needs-human` label added.                |
| `skipped`     | Filtered by ownership/label/dedup; no work performed.          |

---

## Orchestrator interaction

Each attempt **spawns a fresh deterministic Bernstein run** via the
configured `DispatchHook`
(`src/bernstein/core/autofix/dispatcher.py:87-100`). It does **not**
reuse or attach to the original session that opened the PR. Concretely:

- A new top-level `bernstein run --goal "<synthesised>" --model <bandit>`
  is invoked in a clean worktree on the PR head branch.
- The run inherits the autofix-injected cost cap (`cost_cap_usd`) and
  the operator's existing `cost_cap` from `bernstein.yaml` is applied
  multiplicatively (whichever bites first).
- The run inherits the lifecycle/notification/quality-gate stack
  exactly like any other run; failures bubble up through the same
  `post_task` hooks.
- Successful attempts produce a commit on the PR's head branch. The
  daemon honours `allow_force_push` per repo
  (`src/bernstein/core/autofix/config.py:88-90`); when false it falls
  back to a merge commit on the branch tip.
- The synthesised goal is **the only context the spawned run gets** -
  the truncated log and PR metadata. This is intentional, so an attempt
  is reproducible by hand from the audit record.

---

## Configuration

`autofix.toml` lives at `$XDG_CONFIG_HOME/bernstein/autofix.toml`
(default `~/.config/bernstein/autofix.toml`). Source:
`src/bernstein/core/autofix/config.py`.

```toml
poll_interval_seconds = 60
log_byte_budget       = 65536

[[repo]]
name             = "sipyourdrink-ltd/bernstein"
cost_cap_usd     = 5.0
allow_force_push = false
label            = "bernstein-autofix"

[[repo]]
name         = "acme-org/example"
cost_cap_usd = 2.0
```

Top-level keys:

| Key                       | Default | Meaning                                                                |
|---------------------------|---------|------------------------------------------------------------------------|
| `poll_interval_seconds`   | 60      | Tick cadence. Lower = faster reaction, more API quota burn.            |
| `log_byte_budget`         | 65536   | Max bytes of failing log fed to classifier + goal synth (head-truncated). |
| `[[repo]]`                | (req)   | At least one repo entry needed; daemon refuses to start otherwise.     |

Per-repo keys:

| Key                | Default               | Meaning                                                                 |
|--------------------|-----------------------|-------------------------------------------------------------------------|
| `name`             | (req)                 | `OWNER/REPO`. Empty/missing = ValueError.                               |
| `cost_cap_usd`     | 5.0                   | USD ceiling per attempt. 0 = unlimited (don't).                         |
| `label`            | `bernstein-autofix`   | Label that gates whether the daemon may touch a PR.                     |
| `allow_force_push` | false                 | If false, attempts merge-commit on branch tip instead of force-pushing. |

Module-level constants worth knowing:

- `MAX_ATTEMPTS_PER_PUSH` = 3
  (`src/bernstein/core/autofix/config.py:60-61`). Hardcoded - not in
  TOML - because it's a safety rail, not a tuning knob.
- `SESSION_TRAILER_KEY` = `bernstein-session-id`
  (`src/bernstein/core/autofix/ownership.py:38-40`).

---

## Observability

Three places to look for autofix activity:

### 1. Status JSONL - `bernstein autofix attach` and `--watch`

`.sdd/runtime/autofix.jsonl` - one line per dispatched attempt
(`src/bernstein/core/autofix/daemon.py:50, 159-189`):

```json
{
  "ts": 1714843200.123,
  "attempt_id": "abc12345",
  "repo": "sipyourdrink-ltd/bernstein",
  "pr_number": 1042,
  "push_sha": "deadbeef...",
  "run_id": "8765432",
  "session_id": "ses-...",
  "attempt_index": 2,
  "outcome": "success",
  "classifier": "flaky",
  "model": "sonnet",
  "cost_usd": 0.0314,
  "commit_sha": "feedface...",
  "reason": ""
}
```

### 2. Prometheus - `/metrics` endpoint

Two counters (registered in
`src/bernstein/core/autofix/metrics.py`):

- `autofix_attempts_total{repo, outcome, classifier}` - increments per
  dispatched attempt. Labels match the Status JSONL fields.
- `autofix_cost_usd_total{repo}` - increments by per-attempt USD spend.
  `cost_capped` attempts also increment by their pre-cap spend.

### 3. Audit log - `bernstein audit`

Each attempt writes two HMAC-chained records
(`src/bernstein/core/autofix/dispatcher.py:24-28`):

- `autofix.attempt.start` - repo, PR, run_id, classifier, planned model,
  goal hash.
- `autofix.attempt.end` - outcome, commit_sha, actual cost.

Joined by `attempt_id`. The chain is what `dr backup` preserves, so a
restored workspace can be queried for past autofix decisions even after
the JSONL log was rotated.

---

## Safety rails (read once, twice)

- **No greenfield work.** Autofix only touches PRs Bernstein opened. The
  session-id trailer + label gate is a hard double-check.
- **Three-strike rule.** A push SHA gets at most 3 attempts. Beyond
  that, the daemon adds `needs-human` and stays out of the way.
- **Spend cap.** Per-repo `cost_cap_usd` is checked **before**
  dispatch - a runaway repo cannot drain the account.
- **Force-push off by default.** Most teams want history they can
  bisect; force-push only if you have configured it explicitly.
- **No LLM in the scheduling loop.** The dispatcher is plain Python and
  the classifier is regex - escalation decisions are reproducible
  (`src/bernstein/core/autofix/dispatcher.py:1-9`).

---

## Code pointers

- `src/bernstein/cli/commands/autofix_cmd.py` - CLI surface
- `src/bernstein/core/autofix/__init__.py:1-79` - package overview
- `src/bernstein/core/autofix/config.py:1-150` - TOML schema + defaults
- `src/bernstein/core/autofix/classifier.py:1-90` - keyword classifier (security/flaky/config)
- `src/bernstein/core/autofix/ownership.py:1-40` - session-id trailer + label gate
- `src/bernstein/core/autofix/gh_logs.py` - `gh run view --log-failed` wrapper
- `src/bernstein/core/autofix/dispatcher.py:1-100` - per-attempt pipeline
- `src/bernstein/core/autofix/daemon.py:1-484` - process supervisor (start/stop/status/attach + tick_once)
- `src/bernstein/core/autofix/metrics.py` - Prometheus counters
