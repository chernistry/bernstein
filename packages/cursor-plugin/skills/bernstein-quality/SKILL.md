---
name: bernstein-quality
description: >
  Show quality metrics for Bernstein runs — success rates per model,
  lint/test pass rates, completion time distributions. Use when the user
  asks about quality, reliability, which model performs best, or pass rates.
---

# Bernstein Quality Metrics

Analyze quality and reliability of agent-generated code.

## When to Use

- User asks "how reliable are the agents?" or "which model is best?"
- User wants success rates, pass rates, or completion time stats
- User asks about test failures or lint issues across models
- User says "show me quality metrics"

## Instructions

1. Run `scripts/quality.sh metrics` for overall quality metrics.
2. Run `scripts/quality.sh pass-rates` for lint/typecheck/test pass rates by model.
3. Run `scripts/quality.sh times` for completion time distributions.

4. Present a quality dashboard:

```
## Quality Dashboard

### Success Rate by Model
| Model | Tasks | Success | Fail | Rate |
|-------|-------|---------|------|------|
| claude-sonnet-4 | 24 | 22 | 2 | 91.7% |
| gpt-4.1 | 12 | 10 | 2 | 83.3% |

### Pass Rates
| Check | Overall | claude-sonnet-4 | gpt-4.1 |
|-------|---------|-----------------|---------|
| Lint | 96% | 98% | 92% |
| Type-check | 88% | 91% | 83% |
| Tests | 85% | 89% | 75% |

### Completion Times
| Percentile | Time |
|------------|------|
| p50 | 3m 20s |
| p90 | 8m 45s |
| p99 | 15m 12s |
```

5. Highlight any models with significantly lower pass rates.
6. Recommend model routing adjustments if one model consistently underperforms.
