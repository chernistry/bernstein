# Cost Anomaly Detection — Design Spec

**Issue:** [#209](https://github.com/chernistry/bernstein/issues/209)
**Date:** 2026-03-31
**Approach:** Layered detector reading from existing CostTracker + TokenGrowthMonitor

## Overview

New module `src/bernstein/core/cost_anomaly.py` that detects anomalous spending
patterns in real-time during orchestration runs. Uses a hybrid action model:
kill agents for clear loops, stop spawning for budget concerns, log-only for
advisory signals.

## Data Model

### AnomalySignal

Returned by every detection check. Orchestrator dispatches the action.

```python
@dataclass
class AnomalySignal:
    rule: str           # "per_task_ceiling" | "token_ratio" | "burn_rate" | "retry_spiral" | "model_mismatch"
    severity: str       # "info" | "warning" | "critical"
    action: str         # "log" | "stop_spawning" | "kill_agent"
    agent_id: str | None
    task_id: str | None
    message: str
    details: dict[str, Any]
    timestamp: float
```

### CostBaseline

Rolling window of recent task completions, used for per-task ceiling detection.

```python
@dataclass
class TierStats:
    median_cost_usd: float
    p95_cost_usd: float
    sample_count: int

@dataclass
class CostBaseline:
    per_tier: dict[str, TierStats]   # "small" | "medium" | "large"
    token_ratio_median: float
    token_ratio_p95: float
    sample_count: int
    updated_at: float
```

Persisted to `.sdd/metrics/cost_baseline.json`. Updated after every task completion.
Rolling window of last `baseline_window` (default 50) tasks.

### CostAnomalyConfig

```python
@dataclass
class CostAnomalyConfig:
    enabled: bool = True
    per_task_multiplier: float = 3.0          # warning at 3x tier median
    per_task_critical_multiplier: float = 6.0 # kill at 6x tier median
    budget_warn_pct: float = 60.0
    budget_stop_pct: float = 90.0
    token_ratio_max: float = 5.0              # output/input ratio
    token_ratio_min_tokens: int = 5000        # ignore small tasks
    retry_cost_multiplier: float = 2.0        # retry chain vs original
    baseline_window: int = 50                 # rolling window size
    baseline_min_samples: int = 5             # warmup before ceiling active
```

Loaded from `bernstein.yaml` under `cost_anomaly:` key. All fields optional.

## Detection Rules

### 1. Per-Task Ceiling

**Trigger:** Task completion.
**Logic:** Compare task cost to `baseline.per_tier[complexity].median_cost_usd`.
- Cost > `per_task_multiplier` (3x) median → severity=warning, action=log
- Cost > `per_task_critical_multiplier` (6x) median → severity=critical, action=kill_agent
**Warmup:** Requires `baseline_min_samples` (5) completed tasks in that tier. Before warmup, log-only.

### 2. Burn Rate

**Trigger:** Every orchestrator tick.
**Logic:** Read `CostTracker.project()` for `projected_total_usd` vs `budget_usd`.
- Projected > `budget_warn_pct` (60%) of budget → severity=warning, action=log
- Projected > `budget_stop_pct` (90%) of budget → severity=critical, action=stop_spawning
**Note:** Only active when `budget_usd > 0` (budget is configured). Deduplicates
with existing CostTracker thresholds — anomaly detector handles projection-based
warnings, CostTracker keeps its existing actual-spend thresholds.

### 3. Token Ratio

**Trigger:** Every orchestrator tick, per live agent.
**Logic:** For each agent with `tokens_used > token_ratio_min_tokens` (5000):
- Read input/output token counts from agent session
- If `output_tokens / max(input_tokens, 1) > token_ratio_max` (5.0) → severity=critical, action=kill_agent
**Rationale:** High output/input ratio indicates the agent is generating repetitive
output (loop, hallucination spiral). Complements TokenGrowthMonitor's quadratic
growth detection — this catches steady but wasteful generation patterns.

### 4. Retry Spiral

**Trigger:** Task retry starts (checked at task completion when task has retry metadata).
**Logic:** Sum cost of all attempts for same original task ID.
- If `total_retry_cost > original_estimate * retry_cost_multiplier` (2x) → severity=critical, action=stop_spawning
**Note:** Requires retry chain tracking. The detector maintains a `dict[str, float]`
mapping `original_task_id → cumulative_retry_cost`. Cleared when task succeeds or
is abandoned.

### 5. Model Mismatch

**Trigger:** Agent spawn.
**Logic:** Compare task complexity/scope to model tier.
- `complexity in ("trivial", "small")` + model is opus-tier → severity=info, action=log
- `complexity == "medium"` + model is opus-tier → no signal (reasonable)
**Mapping:** Model tier derived from model name: opus/o1/o3 → "heavy", sonnet/gpt-4 → "medium", haiku/flash/mini → "light".
**Advisory only** — never blocks or kills.

## Integration Points

### CostAnomalyDetector class

```python
class CostAnomalyDetector:
    def __init__(self, config: CostAnomalyConfig, workdir: Path) -> None: ...
    def check_tick(self, agents: list[AgentSession], cost_tracker: CostTracker) -> list[AnomalySignal]: ...
    def check_task_completion(self, task: Task, cost_usd: float, tokens_in: int, tokens_out: int) -> list[AnomalySignal]: ...
    def check_spawn(self, task: Task, model: str) -> list[AnomalySignal]: ...
    def load_baseline(self) -> None: ...
    def save_baseline(self) -> None: ...
```

### Orchestrator wiring (3 touch points)

1. **Constructor** (~line 393): Create `CostAnomalyDetector(config.cost_anomaly, workdir)`, call `load_baseline()`.

2. **Tick loop** (~line 1023, after `check_token_growth`):
   ```python
   signals = self._anomaly_detector.check_tick(self._agents, self._cost_tracker)
   for sig in signals:
       self._handle_anomaly(sig)
   ```

3. **Task completion** (`task_completion.py` ~line 638, after cost recording):
   ```python
   signals = orch._anomaly_detector.check_task_completion(task, cost_usd, tokens_in, tokens_out)
   for sig in signals:
       orch._handle_anomaly(sig)
   ```

### Action dispatch (orchestrator method)

```python
def _handle_anomaly(self, signal: AnomalySignal) -> None:
    self._anomaly_detector.record_signal(signal)  # audit trail
    if signal.action == "kill_agent" and signal.agent_id:
        self._kill_agent(signal.agent_id, reason=signal.message)
    elif signal.action == "stop_spawning":
        self._stop_spawning = True
        log.warning("Anomaly: %s — stopping new spawns", signal.message)
    else:
        log.info("Anomaly (advisory): %s", signal.message)
```

### Spawn check (optional, in spawner)

```python
signals = self._anomaly_detector.check_spawn(task, model)
for sig in signals:
    self._handle_anomaly(sig)  # always log-only for model mismatch
```

## Signal Deduplication

Tick-based rules (burn rate, token ratio) run every ~5 seconds. Without dedup they'd
spam identical signals. The detector maintains `_cooldowns: dict[str, float]` mapping
`"{rule}:{agent_id}"` → last signal timestamp. Cooldown periods:

- `kill_agent` signals: no cooldown (act immediately, agent dies after first)
- `stop_spawning` signals: 60s cooldown (log once per minute)
- `log` signals: 300s cooldown (log once per 5 minutes)

## Audit Trail

Every signal appended to `.sdd/metrics/anomalies.jsonl`:

```json
{"ts": 1711872000.0, "rule": "token_ratio", "severity": "critical", "action": "kill_agent", "agent_id": "agent-3", "task_id": "T042", "message": "Token ratio 8.2 exceeds threshold 5.0 (15420 out / 1880 in)", "details": {"ratio": 8.2, "output_tokens": 15420, "input_tokens": 1880, "threshold": 5.0}}
```

## Baseline Update Logic

On every task completion:
1. Append `{tier, cost_usd, token_ratio}` to rolling window (capped at `baseline_window`)
2. Recalculate per-tier median and p95 using `statistics.median` and sorted percentile
3. Recalculate token ratio median and p95
4. Persist to `.sdd/metrics/cost_baseline.json`

First run with no baseline file: all per-task ceiling checks are log-only until warmup.

## Testing

- **Unit tests per rule:** Synthetic data crossing/not crossing thresholds
- **Baseline logic:** Rolling window update, warmup, tier separation, persistence round-trip
- **Signal deduplication:** Same anomaly not re-signaled every tick (cooldown per rule+agent)
- **Integration:** Mock orchestrator tick triggering anomaly → verify agent killed / spawning stopped
- **Edge cases:** Zero budget (burn rate disabled), zero-cost task, missing baseline file, first run
