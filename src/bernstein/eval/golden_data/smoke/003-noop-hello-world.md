---
id: smoke-003-noop-hello-world
title: No-op sanity task — print hello world
role: backend
expected_files_modified: []
expected_test_outcomes: {}
completion_signals:
  - "agent prints the exact string 'hello world' to stdout"
  - "agent exits with status code 0"
  - "no files are modified"
max_cost_usd: 0.05
max_duration_s: 60
owned_files: []
---

Print the exact string `hello world` to stdout and exit with status 0.

Do not create, modify, or delete any file. Do not run any test. This task
is a sanity check that the judge and harness wiring work end-to-end with
the cheapest possible agent action.
