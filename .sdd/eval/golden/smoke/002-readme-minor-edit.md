---
id: smoke-002-readme-minor-edit
title: Make a minor, reversible README copy edit
role: docs
expected_files_modified:
  - README.md
expected_test_outcomes: {}
completion_signals:
  - "README.md diff is <= 3 lines changed"
  - "the change is a copy edit (typo, punctuation, or phrasing tweak) — no semantic or structural change"
  - "no markdown headings or links are altered"
max_cost_usd: 0.10
max_duration_s: 120
owned_files:
  - README.md
---

Find one small, low-risk copy edit in `README.md` — a typo, a missing
period, a stray double space, or an awkward phrasing — and fix only that.

Constraints:

- diff must be <= 3 changed lines,
- do not modify headings, links, code blocks, or tables,
- do not add or remove sections,
- do not reflow paragraphs.

If no such issue exists, exit without modifying any file and report
"no fixable nit found".
