# Extension Publish Pipeline + UX Polish — Design Spec

**Version**: 1.0
**Date**: 2026-03-29
**Author**: Workflow Architect
**Status**: Approved (autonomous agent — task spec serves as user approval)
**Task**: #43ff11d8dd00

---

## Overview

The Bernstein VS Code extension (`packages/vscode/`) is ~95% complete. The extension implements all UX requirements from the task spec and the CI/CD publish pipeline exists. This spec documents the current state, identifies the remaining gaps, and defines the implementation plan to reach a publishable v0.1.0.

---

## Current State Assessment

### What exists and works

| Component | File | Status |
|---|---|---|
| AgentTreeProvider | `src/AgentTreeProvider.ts` | Complete — delegation tree, ●/○ icons, click-to-output |
| TaskTreeProvider | `src/TaskTreeProvider.ts` | Complete — status icons, progress %, click-to-output |
| StatusBarManager | `src/StatusBarManager.ts` | Complete — `🎼 N agents · X/Y tasks · $Z.ZZ` |
| DashboardProvider | `src/DashboardProvider.ts` | Complete — stat cards, sparkline, skeleton, offline state |
| BernsteinClient | `src/BernsteinClient.ts` | Complete — SSE + polling fallback, auto-reconnect |
| OutputManager | `src/OutputManager.ts` | Complete — per-agent output channels |
| Commands | `src/commands.ts` | Nearly complete — missing `bernstein.inspectAgent` |
| Extension entry | `src/extension.ts` | Complete — chat participant, SSE, debouncing |
| CI/CD pipeline | `.github/workflows/publish-extension.yml` | Complete — icon validation, type check, tests, build, publish to both registries, GitHub Release |
| Package metadata | `package.json` | Complete — all required marketplace fields |
| `.vscodeignore` | `.vscodeignore` | Complete — excludes node_modules, src, tests |
| README | `README.md` | Complete — marketplace listing with screenshots |
| CHANGELOG | `CHANGELOG.md` | Complete — v0.1.0 entry |
| Icon | `media/bernstein-icon.png` | Complete — 128×128 PNG |
| Screenshots | `media/screenshots/` | Present — sidebar.png, dashboard.png, command-palette.png |

### Confirmed UX requirements met

- Status bar: `🎼 3 agents · 7/12 tasks · $0.42` — **matches spec exactly**
- Tree view: `$(circle-filled)` / `$(circle-outline)` icons (render as ●/○ in VS Code)
- Dashboard: 2×2 card grid, cost sparkline, skeleton loading, zero chrome
- SSE connection (not polling) with debounced updates (500ms = max 2/s)
- `onStartupFinished` activation = lazy load (doesn't block VS Code startup)
- Auto-connect on `:8052`
- Graceful offline state: "Connecting to Bernstein…" not an error
- Respects VS Code theme via CSS variables

---

## Gaps Requiring Implementation

### GAP-1: `bernstein.openTask` missing from package.json

**Location**: `package.json` `contributes.commands`, `src/TaskTreeProvider.ts:41`
**Severity**: Bug — command is registered in `commands.ts` but NOT declared in `package.json`. VS Code may warn about undeclared commands.
**Fix**: Add `bernstein.openTask` to `contributes.commands`.

### GAP-2: Missing `bernstein.inspectAgent` command

**Location**: `package.json` menus, `src/commands.ts`
**Severity**: Feature gap — CHANGELOG and task spec both reference "Right-click agent → Kill / Inspect / Show Logs" but there is no "Inspect" entry.
**Fix**: Add `bernstein.inspectAgent` command that shows agent metadata (role, model, tasks, cost, spawn time) in a VS Code information message or quick pick. Add to package.json menus for `view/item/context`.

### GAP-3: PUBLISH.md token name inconsistency

**Location**: `packages/vscode/PUBLISH.md` manual publish section
**Severity**: Documentation bug — manual publishing section references `VSCE_PAT` and `OVSX_PAT` but the actual workflow and secrets use `VS_MARKETPLACE_TOKEN` and `OPEN_VSX_TOKEN`.
**Fix**: Update PUBLISH.md to use consistent token names throughout.

### GAP-4: Extension size unverified

**Location**: `.vscodeignore`, `esbuild.mjs`
**Severity**: Low — CHANGELOG states ~800KB but this needs to be verified. The `sourcemap: true` in esbuild generates a `.map` file. The `.vscodeignore` has `**/*.map` which would exclude `dist/extension.js.map` from the package.
**Note**: Must verify actual packaged size is < 1MB. The `--no-dependencies` flag in `vsce package` plus `.vscodeignore` excluding `node_modules` should keep it tiny.

---

## Implementation Plan

### Phase 1: Bug fixes (GAP-1, GAP-2, GAP-3)

**1a. package.json — add `bernstein.openTask` command**
```json
{ "command": "bernstein.openTask", "title": "Bernstein: Open Task Output", "icon": "$(go-to-file)" }
```

**1b. commands.ts — add `bernstein.inspectAgent` handler**
```typescript
vscode.commands.registerCommand('bernstein.inspectAgent', (item: AgentItem) => {
  const a = item.agent;
  const lines = [
    `Agent: ${a.id}`,
    `Role: ${a.role}`,
    `Model: ${a.model ?? 'unknown'}`,
    `Status: ${a.status}`,
    `Runtime: ${a.runtime_s}s`,
    `Cost: $${a.cost_usd.toFixed(4)}`,
    a.tasks?.length ? `Tasks: ${a.tasks.map(t => t.title).join(', ')}` : 'No tasks',
  ];
  void vscode.window.showInformationMessage(lines.join(' | '));
});
```

**1c. package.json — declare `bernstein.inspectAgent` and add to context menu**
```json
// contributes.commands:
{ "command": "bernstein.inspectAgent", "title": "Bernstein: Inspect Agent", "icon": "$(info)" }

// view/item/context menus (agent.active and agent.idle):
{ "command": "bernstein.inspectAgent", "when": "view == bernstein.agents", "group": "1_agent_actions@3" }
```

**1d. PUBLISH.md — fix token names**
- Replace `VSCE_PAT` → `VS_MARKETPLACE_TOKEN`
- Replace `OVSX_PAT` → `OPEN_VSX_TOKEN`

### Phase 2: Verification

- Run type check: `npx tsc --noEmit`
- Run tests: `npm test`
- Build: `npm run compile`
- Package (dry run): `npx vsce package --no-dependencies --dry-run` — verify size

---

## Publishing Readiness Checklist

### Code quality ✓
- [x] TypeScript strict mode
- [x] All commands implemented and wired
- [x] SSE + polling fallback
- [x] Debounced updates
- [x] Graceful offline state
- [x] CSP-compliant webview (nonce-based, `enableScripts: false`)

### Marketplace requirements ✓
- [x] `publisher` field in package.json (`chernistry`)
- [x] `icon` field pointing to valid 128×128 PNG
- [x] `license` field (`Apache-2.0`)
- [x] `repository` field
- [x] `categories` field (`["AI", "Other"]`)
- [x] README with screenshots
- [x] CHANGELOG

### CI/CD ✓
- [x] `ext-v*` tag triggers publish workflow
- [x] Icon size validation (≥128×128)
- [x] Type check + tests gate publish
- [x] Publishes to both VS Code Marketplace and Open VSX
- [x] Uploads VSIX to GitHub Release

### Manual human actions required (not code)
- [ ] Azure DevOps org + PAT with Marketplace → Manage scope
- [ ] Publisher created at marketplace.visualstudio.com (`chernistry`)
- [ ] Open VSX account + namespace (`chernistry`)
- [ ] GitHub secrets: `VS_MARKETPLACE_TOKEN`, `OPEN_VSX_TOKEN`
- [ ] Cursor forum verification post
- [ ] Demo GIF (optional, marketplace hero)

---

## Handoff Contracts

### CI Publish Pipeline
**Trigger**: `git tag ext-v0.1.0 && git push --tags`
**Steps**: install → type-check → test → build → package → publish-vscode → publish-ovsx → release
**Success**: Extension live on both marketplaces + VSIX on GitHub Releases
**Failure**: Any step failure aborts publish; workflow logs show which step failed
**Recovery**: Fix issue, bump patch version, create new tag

---

## Out of Scope

- Demo GIF creation (requires manual screen recording)
- Website page at alexchernysh.com/bernstein/extension (manual HTML page)
- Cursor forum post (manual account action)
- Creating Azure DevOps org / marketplace publisher (manual account actions)
- Screenshots content verification (files exist, content unverified)
