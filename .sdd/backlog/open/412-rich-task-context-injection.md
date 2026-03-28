# 412 -- Rich task context injection to eliminate agent research overhead

**Role:** architect
**Priority:** 1 (critical)
**Scope:** large
**Complexity:** high

## Problem
Every spawned agent starts fresh and spends significant time (and tokens) reading files, running `find`, `rg`, `cat` to understand the codebase before doing actual work. The manager creates tasks with titles and short descriptions, but agents lack:
- File structure relevant to the task
- Contents of key files they'll need to modify
- Architecture context specific to the subsystem
- Results/decisions from prior agents working on related tasks

This wastes 30-50% of agent turns on "orientation" that the orchestrator already knows.

## Implementation

### 1. Context builder (`src/bernstein/core/context.py`)
Create a `TaskContextBuilder` that enriches task descriptions before spawning:

- **File map**: For each `owned_files` entry, include a brief summary (first docstring, class/function signatures). Generate via AST parsing, not LLM.
- **Dependency graph**: For each owned file, list imports and importers (who calls this code).
- **Related files**: Files frequently co-modified with owned files (from git log --follow).
- **Subsystem context**: If task touches `evolution/`, include the evolution module map. If `core/`, include core architecture. Derived from directory-level README or module docstrings.

### 2. Manager enrichment
When the manager agent creates tasks via POST /tasks:
- Manager should include `context_hints` field: list of file paths, function names, architectural notes relevant to the task.
- Update manager role prompt to instruct: "For each task you create, include a `context_hints` section listing the specific files, functions, and architectural decisions the assigned agent needs to know."

### 3. Prompt injection in spawner
In `_render_prompt()`, after the task block, inject:
```
### Context (auto-generated)
#### Files you'll work with:
- src/bernstein/evolution/loop.py: EvolutionLoop class, runs 5-min cycles...
  - Key functions: run_cycle(), _detect_opportunities(), _generate_proposals()
  - Imports: detector, proposals, sandbox, gate, applicator
  - Imported by: orchestrator.py

#### Related recent changes:
- commit abc123: "Fix proposal scoring" — changed detector.py scoring logic
- commit def456: "Add EWMA to aggregator" — new trend detection

#### Architecture notes:
- Evolution pipeline: Detect → Propose → Sandbox → Gate → Apply
- Risk levels L0-L3, only L0-L1 auto-apply
```

### 4. Shared knowledge base (`.sdd/knowledge/`)
- `.sdd/knowledge/architecture.md` — auto-generated module map with signatures
- `.sdd/knowledge/recent_decisions.md` — last 10 completed tasks with summaries
- `.sdd/knowledge/file_index.json` — file → {summary, exports, imports, last_modified}
- Regenerated periodically by orchestrator (every 5 cycles or on significant changes)
- Included in agent prompts as compressed context

### 5. Post-task knowledge capture
When an agent completes a task:
- Extract key decisions/findings from the agent's output log
- Append to `.sdd/knowledge/recent_decisions.md`
- Update `file_index.json` for modified files

## Files
- src/bernstein/core/context.py — TaskContextBuilder (new or extend existing)
- src/bernstein/core/spawner.py — inject rich context into prompts
- src/bernstein/core/orchestrator.py — trigger knowledge base refresh
- templates/roles/manager/system_prompt.md — instruct manager to include context_hints
- tests/unit/test_context_builder.py (new)

## Completion signals
- test_passes: uv run pytest tests/unit/test_context_builder.py -x -q
- file_contains: src/bernstein/core/context.py :: TaskContextBuilder
- file_contains: src/bernstein/core/spawner.py :: context_builder
- path_exists: .sdd/knowledge/file_index.json
