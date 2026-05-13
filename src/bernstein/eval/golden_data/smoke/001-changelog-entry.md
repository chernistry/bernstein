---
id: smoke-001-changelog-entry
title: Append a CHANGELOG entry under Unreleased
role: docs
expected_files_modified:
  - CHANGELOG.md
expected_test_outcomes: {}
completion_signals:
  - "CHANGELOG.md contains a new bullet under the '## Unreleased' section"
  - "the new bullet describes a single concrete change in one short sentence"
max_cost_usd: 0.10
max_duration_s: 120
owned_files:
  - CHANGELOG.md
---

Append a single bullet entry under the existing `## Unreleased` section in
`CHANGELOG.md`. The bullet must:

- describe one concrete change in a single short sentence,
- start with a verb (e.g. "add", "fix", "update"),
- not modify any other section of the file,
- not introduce trailing whitespace.

Do not edit any other file. Do not rewrite history above `## Unreleased`.
