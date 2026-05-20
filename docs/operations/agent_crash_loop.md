# Agent crash loops and the parked state

When an agent fails to spawn, the supervisor does not give up on the
first failure nor retry forever. It applies a bounded respawn budget. A
session that crash-loops past that budget is parked: the supervisor
refuses to respawn it until an operator intervenes. This turns a noisy
crash loop into a single, auditable failure mode.

## TL;DR

| Concept | Default | Notes |
|---------|---------|-------|
| Respawn budget | 3 respawns / 60s window | Initial spawn is not counted |
| Backoff | `500ms * attempt`, capped at `5s` | Linear growth |
| Window reset | Rolling | Respawns older than the window fall out of the count |
| On exhaustion | Session is parked | `AgentStartupExhausted` event published |
| Recovery | Operator-driven only | `bernstein agents resume <id>` |

## How the budget works

1. The first spawn of a session is the initial spawn. It never consumes
   budget.
2. Every failed respawn inside the rolling window consumes one unit of
   budget and waits the linear backoff before retrying.
3. Backoff is `initial_backoff_ms * attempt`, capped at `max_backoff_ms`.
   With defaults that is 500ms, 1000ms, 1500ms, ... up to 5000ms.
4. The window is rolling. A session that recovers and stays up long
   enough for old respawn timestamps to age out of the window regains
   its full budget without any operator action.
5. When the number of respawns inside the window reaches `max_respawns`,
   the next failure parks the session.

## The parked state

A parked session is terminal until resumed. The supervisor:

- transitions the session to `parked`;
- publishes a single `agent.startup_exhausted` lifecycle event carrying
  `reason`, `last_error`, `attempts`, `window_seconds`, and
  `max_respawns`;
- refuses any further spawn with `SessionParkedError`.

The park reason is always `respawn_budget_exhausted`. The persistent
crash loop almost always means a real fault: a missing adapter binary,
bad configuration, or an expired token. Read `last_error` first.

## Inspecting parked sessions

```
bernstein agents parked      # list parked sessions
bernstein ps                 # running agents, with a parked footer
```

Both surface the same set of parked session ids backed by the
process-wide supervisor.

## Resuming a session

After fixing the root cause, reset the budget and clear the parked
state:

```
bernstein agents resume <id>
```

Resume is the only recovery path. There is no automatic remediation on
park; that is intentional, so an operator confirms the fault is gone
before the session is allowed to spawn again. Resuming clears the
respawn window, so the session starts again with a full budget.

## Tuning the budget

`RespawnBudget` accepts `max_respawns`, `window_seconds`,
`initial_backoff_ms`, and `max_backoff_ms`. Widen the window or raise
the ceiling for environments with known transient flakiness; tighten
them where a fast park is preferable to repeated retries.
