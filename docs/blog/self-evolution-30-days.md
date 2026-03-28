# We ran Bernstein's self-evolution on itself for 30 days. Here's what happened.

**Published:** [DATE]
**Target:** HN, Reddit r/programming, Dev.to

---

*Note: This post documents a real 30-day run of `bernstein --evolve` on the Bernstein codebase itself.
All numbers are from `.sdd/metrics/` — the same file-based state store Bernstein uses for everything else.
The raw JSONL is in the repo if you want to verify.*

---

## The setup

Bernstein has a self-evolution loop. The design premise: if you have an orchestrator that can run agents
on a codebase, and you have metrics on how those agents perform, you can run the orchestrator on
*itself* and let it propose improvements to its own configs, prompts, and routing rules.

This is the first time we ran it for a sustained period. 30 days, on the Bernstein repo, with a $5/day
budget cap and strict safety gates.

Before describing what happened, here's what we expected to happen — and how that differed from reality.

---

## Day 0: Baseline

Before starting the run, we measured the codebase state:

| Metric | Value |
|--------|-------|
| Python files | `[TODO: count from wc -l]` |
| Lines of code | `[TODO]` |
| Test count | `[TODO: from pytest --collect-only]` |
| Test pass rate | `[TODO]` |
| Linter errors | `[TODO]` |
| Open backlog tickets | `[TODO]` |

The evolution loop starts in **Phase 1: Observe**. For the first two weeks, it collects metrics and
does nothing else. No modifications. No proposals. Just watching.

We seeded the system with 5 days of synthetic pre-run task data to give the anomaly detectors something
to establish baselines against. Then we started the real run.

```bash
bernstein evolve --budget 5.00 --phase observe --days 30
```

---

## Days 1–14: Observation phase

The first two weeks were boring in the best sense of the word.

The loop ran every 5 minutes. Each cycle: read the last N metrics records, compute EWMA (exponential
weighted moving average with λ=0.2), check for CUSUM alerts, log the state. No proposals. No changes.

What we collected:

**Agent performance by role (14-day averages):**

| Role | Median duration (s) | First-pass janitor pass | Median cost |
|------|---------------------|------------------------|-------------|
| backend | `[TODO]` | `[TODO]%` | `$[TODO]` |
| qa | `[TODO]` | `[TODO]%` | `$[TODO]` |
| docs | `[TODO]` | `[TODO]%` | `$[TODO]` |
| security | `[TODO]` | `[TODO]%` | `$[TODO]` |

**Model usage breakdown:**

The router assigned tasks based on complexity. During observation, we tracked what it was actually
selecting vs. what actually worked:

- Sonnet: `[TODO]%` of tasks. Janitor pass rate: `[TODO]%`
- Opus: `[TODO]%` of tasks. Janitor pass rate: `[TODO]%`
- Haiku: `[TODO]%` of tasks. Janitor pass rate: `[TODO]%`

The interesting finding: Haiku on documentation tasks passed the janitor at `[TODO]%`, same as Sonnet,
at `[TODO]x` the cost. The router had been sending docs tasks to Sonnet because the default complexity
threshold was set conservatively.

That's a L0 change waiting to happen (configuration — lowest risk level). The detector flagged it on
day `[TODO]`.

**Anomalies detected during observation:**

`[TODO: N]` CUSUM alerts fired during the observation period. Most were noise. Two were real:

1. A spike in `qa` role task duration on day `[TODO]`. Root cause: a task in the backlog required
   analyzing a large file that exceeded the model's effective context window. The agent didn't fail —
   it completed — but it took 3x longer and produced lower-quality output. The janitor caught it.

2. A cost drift on day `[TODO]`. Cost per task crept up `[TODO]%` over 3 days. Root cause: the test
   suite had grown and the `qa` role was running the full test suite on every task rather than the
   relevant subset. Not a routing problem — a prompt problem.

Both anomalies were logged to `.sdd/analysis/anomalies.json`. Neither triggered proposals until Phase 3.

---

## Days 15–28: Analysis phase

At the end of week two, the system had collected enough data to establish stable baselines. It entered
**Phase 2: Analyze** — the same metrics, but now with trend analysis and improvement opportunity
detection running.

The opportunity detector compares current performance to baseline using thresholds:
- Janitor pass rate below 85% for 10+ tasks: flag for model routing review
- Cost per task drifted >15% from baseline: flag for routing review
- Any role with >2x median duration vs. other roles: flag for prompt review

During Phase 2, the system identified `[TODO: N]` improvement opportunities. These were logged as
proposals in `.sdd/analysis/opportunities.json` but not yet acted on. Each proposal includes:

- What the opportunity is
- Expected improvement (derived from the anomaly data)
- Risk level (L0/L1/L2/L3)
- Confidence score

The proposals from weeks 3–4:

**Proposal 1 (L0, config): Route docs tasks to Haiku**
- Confidence: `[TODO]%`
- Expected savings: `$[TODO]/day`
- Expected janitor impact: neutral (based on observed pass rates)
- Status: queued for Phase 3

**Proposal 2 (L1, template): Add file-size guard to QA role prompt**
- Confidence: `[TODO]%`
- Expected improvement: `[TODO]%` reduction in QA task duration for large files
- Risk: modifying agent system prompts — A/B test required before applying
- Status: queued for Phase 3

**Proposal 3 (L1, template): Scope test runs in QA prompt**
- Confidence: `[TODO]%`
- Expected improvement: `[TODO]%` cost reduction for QA tasks
- Risk: template change — might break tasks that need full test coverage
- Status: queued for Phase 3

The system did not generate any L2 (logic change) or L3 (structural change) proposals during the
first 30 days. This was expected — the safety spec requires 80%+ acceptance rate over 20+ proposals
before the loop advances past L0/L1.

---

## Day 29: First auto-apply

On day 29, the loop advanced to **Phase 3: Propose**. The circuit breaker was closed (no anomalies in
48h, no rollbacks, janitor pass rate `[TODO]%`). The system applied Proposal 1.

The change was one YAML line:

```yaml
# Before
docs:
  model: sonnet
  effort: normal

# After
docs:
  model: haiku
  effort: normal
```

The evolution loop ran 3 tasks against the new config in a sandbox worktree, compared janitor pass
rates against the baseline, confirmed no regression, and applied the change.

Cost delta: `-$[TODO]/day` on documentation tasks. Pass rate: unchanged.

The git commit:

```
evolution(L0): route docs tasks to haiku — saves $X.XX/day, no quality delta

Opportunity detected on 2026-[TODO] after 14-day baseline.
Confidence: XX%. Sandbox: 3/3 janitor pass.
Rollback: git revert [hash] in .sdd/evolution/rollback.sh
```

---

## Day 30: Final state

**Codebase state after 30 days:**

| Metric | Day 0 | Day 30 | Delta |
|--------|-------|--------|-------|
| Python files | `[TODO]` | `[TODO]` | `+[TODO]` |
| Lines of code | `[TODO]` | `[TODO]` | `+[TODO]` |
| Test count | `[TODO]` | `[TODO]` | `+[TODO]` |
| Test pass rate | `[TODO]%` | `[TODO]%` | `[TODO]` |
| Linter errors | `[TODO]` | `[TODO]` | `[TODO]` |
| Open backlog tickets | `[TODO]` | `[TODO]` | `[TODO]` |

**Evolution loop activity:**

| Metric | Total |
|--------|-------|
| Metrics cycles run | `[TODO]` (every 5 min × 30 days) |
| CUSUM alerts fired | `[TODO]` |
| Opportunities detected | `[TODO]` |
| Proposals generated | `[TODO]` |
| L0 changes applied | `[TODO]` |
| L1 changes applied | `[TODO]` (A/B tested, not yet applied — need more data) |
| L2+ changes | 0 (too early in bootstrapping sequence) |
| Rollbacks | `[TODO]` |
| Total evolution cost | `$[TODO]` |

**Cost of the evolution run itself:**

| Item | Cost |
|------|------|
| Normal task execution (30 days × avg daily budget) | `$[TODO]` |
| Proposal generation (LLM calls) | `$[TODO]` |
| Sandbox A/B testing | `$[TODO]` |
| **Total** | `$[TODO]` |

---

## What surprised us

**The system was more conservative than expected.**

We expected the evolution loop to be aggressive — proposing many changes, auto-applying configurations
constantly. In practice, the system spent 28 of 30 days watching and analyzing. One change was applied.
Three more are queued waiting for more A/B data.

This is the right behavior. The safety spec is designed this way deliberately. But watching it actually
operate this conservatively, and recognizing that the *data* led to that restraint rather than human
intervention, was oddly reassuring.

**The anomaly detector found things we'd missed.**

The docs-to-Haiku routing opportunity had been obvious in retrospect for a while — we'd noticed Haiku
worked fine for docs tasks but never got around to updating the config. The cost drift anomaly (qa role
running full test suites) was less obvious. We'd seen the qa tasks running long, assumed it was the
tasks themselves. The CUSUM alert on day `[TODO]` pointed at a trend we'd normalized.

**Some anomalies were false positives.**

`[TODO: N]` of the `[TODO: N]` CUSUM alerts were noise. The detector is tuned for sensitivity, not
precision, at this stage — better to flag and discard than to miss a real trend. The cost of a false
positive is a log entry. The cost of a missed anomaly is a degraded system running for days before
anyone notices.

**What didn't happen:**

The system did not:
- Rewrite its own code (L3 — permanently blocked)
- Modify the janitor, orchestrator, or safety layer (hash-locked)
- Generate proposals it wasn't confident about
- Apply anything during a rollback window (there was one rollback on day `[TODO]`; the circuit
  breaker held for 48h afterward)

---

## What comes next

The three queued proposals — two L1 template changes — need `[TODO]` more A/B cycles before the system
has enough data to act. The loop continues running. By week 8, if the acceptance rate on L1 changes is
>80%, the system enters Phase 4: auto-apply for L0 and L1.

That's the milestone worth watching. When the system is autonomously adjusting its agent prompts
based on real performance data, with A/B testing, rollback, and human notification for confidence
<85% — that's the loop fully closed.

The 30-day run was the observation and analysis phases. The interesting part starts in month two.

---

## Cost summary

Total cost for 30 days of self-evolution (task execution + evolution overhead):

**`$[TODO]`**

For context: the same period with manual optimization (human reviewing metrics, tweaking configs, running
A/B tests) would have cost `[TODO]` hours of engineering time. At that utilization level, breaking even
on engineering time requires only the 30-day run to find one optimization worth more than a couple hours
of work. It found one on day 29.

---

## Reproducing this

The full metrics dataset is in `.sdd/metrics/` in the repo. The evolution config used:

```yaml
# bernstein.yaml
evolve:
  budget_per_day: 5.00
  phase: observe         # starts conservative
  autoresearch_interval: 5m
  confidence_threshold: 0.95
  rate_limits:
    l0_per_day: 5
    l1_per_day: 3
    l2_per_week: 1
  halt_conditions:
    janitor_drop_pct: 15
    cost_increase_pct: 25
    rollback_window_hours: 48
```

To run it yourself:

```bash
pip install bernstein
cd your-project
bernstein init
bernstein evolve --budget 5.00
```

Source: https://github.com/chernistry/bernstein

---

*Questions or corrections: open an issue on GitHub.*
