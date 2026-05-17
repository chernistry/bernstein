---
id: feat-rich-example
created: 2026-04-01
status: in_progress
priority: P0
effort: L
owner: alice
tags: ["cli", "sdd"]
acceptance_criteria:
  - "Schema is loadable"
  - "Validator exits 1 on failure"
success_metric:
  name: "ticket_lint_pass_rate"
  current: 0.6
  target: 0.95
  window_days: 14
evidence:
  - source: ".sdd/audit/2026-05-17.md"
    rows_cited: 12
    value: "p95 < 0.05"
risk: "low - schema is additive"
rice:
  reach: 100
  impact: 2.0
  confidence: 0.8
  effort_days: 1.5
  score: 106.7
ladder_to: "operator-trust"
---

# rich valid ticket

Used to exercise the recommended-key warning suppression path.
