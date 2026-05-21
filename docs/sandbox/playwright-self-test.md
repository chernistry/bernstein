# Playwright self-test

Bernstein's `playwright_runner` drives a local Chromium browser against
the project's dev server, captures screenshots, console messages, and
network errors, and (optionally) hands the result to the existing LLM
judge. The structured output is fed back to the agent so it can see
what its own UI changes actually rendered.

## Module

`src/bernstein/core/sandbox/playwright_runner.py` -
`PlaywrightRunner`, `PlaywrightScenario`, `PlaywrightStep`,
`PlaywrightRunResult`, plus the `load_scenarios` YAML helper.

CLI: `bernstein sandbox web-test`
(`src/bernstein/cli/commands/sandbox_cmd.py`).

## Installation

Playwright is an **optional** dependency. The module imports cleanly
without it; only invoking `PlaywrightRunner.run` raises
`PlaywrightUnavailableError` when the SDK is missing.

```shell
pip install playwright
playwright install chromium
```

## Scenarios YAML

A scenarios file is either a top-level list or a mapping with a
`scenarios:` key. Each scenario has a `name`, an optional
`expectation` (one line of English, surfaced to the judge), and an
ordered list of `steps`.

Step types supported in v1:

| type             | required fields            | notes                                                              |
| ---------------- | -------------------------- | ------------------------------------------------------------------ |
| `navigate`       | `url`                      | Relative paths are resolved against `--url`.                       |
| `click`          | `selector`                 | CSS / Playwright selector.                                         |
| `type`           | `selector`, `text`         | Uses `page.fill` for deterministic input.                          |
| `assert_visible` | `selector`                 | Waits for the element to become visible.                           |
| `screenshot`     | (`name` optional)          | Captures a full-page screenshot. Failed steps also screenshot.     |

`timeout_ms` is overridable per step; default is 30000.

```yaml
scenarios:
  - name: Home page renders
    expectation: hero text is visible
    steps:
      - {type: navigate, url: /}
      - {type: assert_visible, selector: h1}
      - {type: screenshot, name: hero.png}
```

## CLI

```shell
bernstein sandbox web-test <task-id> \
  --url http://localhost:5173 \
  --scenarios scenarios.yaml \
  [--output-dir .sdd/sandbox/<task-id>/] \
  [--judge] \
  [--judge-model anthropic/claude-sonnet-4] \
  [--judge-provider openrouter_free] \
  [--headed] \
  [--json]
```

The command exits non-zero if any scenario fails. Without `--json` it
prints the structured self-test block (the same format intended to be
embedded in the agent's next prompt).

## Output layout

Artefacts land under `--output-dir` (default `.sdd/sandbox/<task-id>/`):

```
.sdd/sandbox/<task-id>/
  <scenario-slug>/
    step-001-navigate.png         # diagnostic shot on failure
    hero.png                      # named screenshot step
    console.jsonl                 # one captured console message per line
    network-errors.jsonl          # one requestfailed payload per line
    scenario-result.json
  run-summary.json
```

## Judge integration

`--judge` constructs an `EvalJudge`
(`bernstein.eval.judge.EvalJudge`) and dispatches a single
`dual_attempt(...)` call with a prompt that:

- restates the agent's task,
- summarises each scenario's pass/fail outcome,
- references screenshot paths,
- enumerates captured console / network errors.

The judge returns a `JudgeVerdict` with the same 0-5 axes as the
code-review judge. Verdict failures do not crash the runner; the
verdict simply attaches as `None`.

## Agent integration

The recommended call site is the post-task review step of the agent
that owns the change. After Bernstein commits the agent's diff:

1. Start the dev server inside the existing sandbox (`bernstein
   preview start`).
2. Run `bernstein sandbox web-test <task-id> --url <preview-url>
   --scenarios <yaml>`.
3. Append the printed self-test block to the agent's next prompt
   under a `## Playwright self-test` section.

The block is intentionally tight: one fact per line, screenshots
referenced by path, judge verdict last. The agent harness is
responsible for surfacing the screenshot bytes when the model supports
images.

## Out of scope (v1)

- Mobile-app self-testing.
- SSIM / pixel-diff visual regressions without an LLM judge.
- Cross-browser matrix (Chromium only).
