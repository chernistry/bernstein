# 610 — TUI Session Manager

**Role:** frontend
**Priority:** 2 (high)
**Scope:** medium
**Depends on:** none

## Problem

There is no visual way to monitor multiple agents running simultaneously. Claude Squad (6K stars) proved that TUI-based multi-agent session management is a compelling developer experience. Bernstein's current output is a flat log stream that makes it impossible to track individual agent progress.

## Design

Build a TUI session manager using Textual or similar Python TUI framework. The interface shows agent outputs side-by-side in resizable panes, with status indicators (idle, running, success, failed) per agent. Include a task list panel showing the backlog with real-time status updates. Add log tailing per agent with scroll-back. Support keyboard navigation: switch focus between panes, pause/resume log scrolling, kill individual agents. This becomes the "wow" demo moment — the visual proof that multi-agent orchestration works. The TUI connects to the task server API for real-time data.

## Files to modify

- `src/bernstein/cli/live.py` (enhance or rewrite)
- `src/bernstein/tui/app.py` (new)
- `src/bernstein/tui/panels.py` (new)
- `src/bernstein/tui/styles.css` (new — Textual CSS)
- `pyproject.toml` (add textual dependency)

## Completion signal

- `bernstein live` launches a TUI with side-by-side agent panes
- Agent status updates in real-time from the task server
- Keyboard navigation works for pane switching and log scrolling
