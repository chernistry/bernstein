# AGENTS.md

## Project

Bernstein — multi-agent orchestrator for CLI coding agents. Python 3.12+, FastAPI task server, deterministic scheduler.

## Setup

```bash
uv venv && uv pip install -e ".[dev]"
```

## Testing

```bash
uv run python scripts/run_tests.py -x   # all tests (isolated per-file runner)
uv run pytest tests/unit/               # unit only
uv run pytest tests/unit/test_foo.py -x -q  # single file
```

## Linting & type checking

```bash
uv run ruff check src/
uv run ruff format src/
uv run pyright src/
```

## Code style

- Python 3.12+, type hints on all public functions
- Max line length: 120
- Use `ruff` rules: E, F, W, I, UP, B, SIM, TCH, RUF
- Pydantic models for data structures
- No LLM calls in orchestrator/scheduler code — only inside agent adapters

## Architecture rules

- Deterministic orchestrator — scheduling decisions are pure Python, never LLM
- Short-lived agents — spawn per task batch (1-3 tasks), exit when done
- File-based state in `.sdd/` — no databases, survives crashes, git-friendly
- Pluggable adapters — new CLI agents via `src/bernstein/adapters/base.py` ABC
- File ownership — each agent task specifies which files it may edit

## Key directories

```
src/bernstein/
├── adapters/      # CLI agent adapters (claude, codex, gemini, qwen, generic)
├── cli/           # Click CLI entry point
├── core/          # orchestrator, server, spawner, janitor, evolution, models
├── evolution/     # circuit breaker, invariants guard, safety types
└── templates/     # Jinja2 role prompt templates
tests/
├── unit/          # fast, no network
└── integration/   # requires running server
```

## PR instructions

- Branch from `main`
- Title: concise, imperative mood ("Add X", "Fix Y")
- Run `uv run ruff check src/ && uv run pyright src/ && uv run python scripts/run_tests.py -x` before committing
- One logical change per PR

## What to work on

Check issues labeled `good first issue` or `help wanted`. The `.sdd/backlog/open/` directory also contains task files with YAML frontmatter describing work items.
