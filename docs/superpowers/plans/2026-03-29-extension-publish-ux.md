# Extension Publish Pipeline + UX Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 3 remaining code gaps in the Bernstein VS Code extension so it is fully ready to publish to VS Code Marketplace and Open VSX.

**Architecture:** The extension is 95% complete. Three targeted fixes close the remaining gaps: (1) declare `bernstein.openTask` in package.json, (2) add `bernstein.inspectAgent` command with test coverage, (3) fix token name inconsistency in PUBLISH.md. The CI/CD publish workflow is already complete and correct.

**Tech Stack:** TypeScript, VS Code Extension API, Jest/ts-jest, esbuild, `@vscode/vsce`

---

## File Map

| File | Change type | Reason |
|---|---|---|
| `packages/vscode/package.json` | Modify | Add `bernstein.openTask` + `bernstein.inspectAgent` to commands; add Inspect to context menus |
| `packages/vscode/src/commands.ts` | Modify | Add `bernstein.inspectAgent` handler |
| `packages/vscode/src/__tests__/commands.test.ts` | Create | Tests for `bernstein.inspectAgent` |
| `packages/vscode/PUBLISH.md` | Modify | Fix token names: `VSCE_PAT` → `VS_MARKETPLACE_TOKEN`, `OVSX_PAT` → `OPEN_VSX_TOKEN` |

---

## Task 1: Declare missing command in package.json

**Files:**
- Modify: `packages/vscode/package.json`

- [ ] **Step 1: Add `bernstein.openTask` to `contributes.commands`**

Open `packages/vscode/package.json`. In the `contributes.commands` array (after the last entry), add:
```json
{ "command": "bernstein.openTask", "title": "Bernstein: Open Task Output", "icon": "$(go-to-file)" }
```

The full commands array should now be:
```json
"commands": [
  { "command": "bernstein.start", "title": "Bernstein: Start", "icon": "$(play)" },
  { "command": "bernstein.refresh", "title": "Bernstein: Refresh", "icon": "$(refresh)" },
  { "command": "bernstein.showDashboard", "title": "Bernstein: Show Dashboard", "icon": "$(browser)" },
  { "command": "bernstein.killAgent", "title": "Bernstein: Kill Agent", "icon": "$(stop)" },
  { "command": "bernstein.showAgentOutput", "title": "Bernstein: Show Agent Output", "icon": "$(output)" },
  { "command": "bernstein.cancelTask", "title": "Bernstein: Cancel Task", "icon": "$(x)" },
  { "command": "bernstein.prioritizeTask", "title": "Bernstein: Prioritize Task", "icon": "$(arrow-up)" },
  { "command": "bernstein.openTask", "title": "Bernstein: Open Task Output", "icon": "$(go-to-file)" }
]
```

- [ ] **Step 2: Verify JSON is valid**

```bash
cd packages/vscode && node -e "require('./package.json'); console.log('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 3: Commit**

```bash
cd packages/vscode
git add package.json
git commit -m "fix(ext): declare bernstein.openTask command in package.json"
```

---

## Task 2: Add `bernstein.inspectAgent` — test first

**Files:**
- Create: `packages/vscode/src/__tests__/commands.test.ts`
- Modify: `packages/vscode/src/commands.ts`
- Modify: `packages/vscode/package.json`

- [ ] **Step 1: Write the failing test**

Create `packages/vscode/src/__tests__/commands.test.ts`:

```typescript
import * as vscode from 'vscode';
import { registerCommands } from '../commands';
import type { BernsteinClient } from '../BernsteinClient';
import type { OutputManager } from '../OutputManager';
import type { AgentItem } from '../AgentTreeProvider';
import type { BernsteinAgent } from '../BernsteinClient';

const BASE_AGENT: BernsteinAgent = {
  id: 'backend-abc123def456',
  role: 'backend',
  status: 'working',
  cost_usd: 0.1234,
  runtime_s: 90,
  model: 'sonnet',
  tasks: [{ id: 't1', title: 'Write tests', status: 'in_progress', progress: 50 }],
};

function makeAgentItem(agent: BernsteinAgent): AgentItem {
  // AgentItem extends vscode.TreeItem — create a minimal stub that satisfies AgentItem usage in commands
  return { agent } as unknown as AgentItem;
}

describe('bernstein.inspectAgent', () => {
  let registeredCommands: Record<string, (...args: unknown[]) => unknown>;
  let mockClient: Partial<BernsteinClient>;
  let mockOutputManager: Partial<OutputManager>;
  let mockContext: Partial<vscode.ExtensionContext>;

  beforeEach(() => {
    registeredCommands = {};
    jest.mocked(vscode.commands.registerCommand).mockImplementation(
      (id: string, handler: (...args: unknown[]) => unknown) => {
        registeredCommands[id] = handler;
        return { dispose: jest.fn() };
      }
    );

    mockClient = {
      baseUrl: 'http://127.0.0.1:8052',
      killAgent: jest.fn(),
      cancelTask: jest.fn(),
      prioritizeTask: jest.fn(),
    };

    mockOutputManager = {
      show: jest.fn(),
    };

    mockContext = {
      subscriptions: [],
    };

    registerCommands(
      mockContext as vscode.ExtensionContext,
      mockClient as BernsteinClient,
      mockOutputManager as OutputManager,
      jest.fn(),
    );
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  it('registers bernstein.inspectAgent command', () => {
    expect(registeredCommands['bernstein.inspectAgent']).toBeDefined();
  });

  it('calls showInformationMessage with agent id', () => {
    const item = makeAgentItem(BASE_AGENT);
    registeredCommands['bernstein.inspectAgent'](item);
    expect(vscode.window.showInformationMessage).toHaveBeenCalledWith(
      expect.stringContaining('backend-abc123def456')
    );
  });

  it('includes role in message', () => {
    const item = makeAgentItem(BASE_AGENT);
    registeredCommands['bernstein.inspectAgent'](item);
    expect(vscode.window.showInformationMessage).toHaveBeenCalledWith(
      expect.stringContaining('backend')
    );
  });

  it('includes cost in message', () => {
    const item = makeAgentItem(BASE_AGENT);
    registeredCommands['bernstein.inspectAgent'](item);
    expect(vscode.window.showInformationMessage).toHaveBeenCalledWith(
      expect.stringContaining('$0.1234')
    );
  });

  it('includes task titles when agent has tasks', () => {
    const item = makeAgentItem(BASE_AGENT);
    registeredCommands['bernstein.inspectAgent'](item);
    expect(vscode.window.showInformationMessage).toHaveBeenCalledWith(
      expect.stringContaining('Write tests')
    );
  });

  it('shows "No tasks" when agent has no tasks', () => {
    const item = makeAgentItem({ ...BASE_AGENT, tasks: [] });
    registeredCommands['bernstein.inspectAgent'](item);
    expect(vscode.window.showInformationMessage).toHaveBeenCalledWith(
      expect.stringContaining('No tasks')
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd packages/vscode && npx jest src/__tests__/commands.test.ts -t "bernstein.inspectAgent" --no-coverage
```

Expected: FAIL — `bernstein.inspectAgent` is not registered

- [ ] **Step 3: Add `bernstein.inspectAgent` to `commands.ts`**

Open `packages/vscode/src/commands.ts`. After the `bernstein.prioritizeTask` registration (the last `registerCommand` call before the final `)`), add:

```typescript
    vscode.commands.registerCommand(
      'bernstein.inspectAgent',
      (item: AgentItem) => {
        const a = item.agent;
        const runtime =
          a.runtime_s > 60
            ? `${Math.floor(a.runtime_s / 60)}m`
            : `${a.runtime_s}s`;
        const taskList = a.tasks?.length
          ? a.tasks.map((t) => t.title).join(', ')
          : 'No tasks';
        const msg = [
          `Agent: ${a.id}`,
          `Role: ${a.role}`,
          `Model: ${a.model ?? 'unknown'}`,
          `Status: ${a.status}`,
          `Runtime: ${runtime}`,
          `Cost: $${a.cost_usd.toFixed(4)}`,
          `Tasks: ${taskList}`,
        ].join(' | ');
        void vscode.window.showInformationMessage(msg);
      },
    ),
```

The full `registerCommands` function body (with all registrations in order):
```typescript
export function registerCommands(
  context: vscode.ExtensionContext,
  client: BernsteinClient,
  outputManager: OutputManager,
  onRefresh: () => void,
): void {
  context.subscriptions.push(

    vscode.commands.registerCommand('bernstein.start', () => {
      const terminal = vscode.window.createTerminal({ name: 'Bernstein' });
      terminal.show();
      terminal.sendText('bernstein run');
    }),

    vscode.commands.registerCommand('bernstein.refresh', onRefresh),

    vscode.commands.registerCommand('bernstein.showDashboard', () => {
      DashboardProvider.openInBrowser(client.baseUrl);
    }),

    vscode.commands.registerCommand(
      'bernstein.killAgent',
      async (item: AgentItem) => {
        const answer = await vscode.window.showWarningMessage(
          `Kill agent ${item.agent.id}?`,
          { modal: true },
          'Kill',
        );
        if (answer === 'Kill') {
          try {
            await client.killAgent(item.agent.id);
            vscode.window.showInformationMessage(
              `Kill signal sent to ${item.agent.id}`,
            );
            onRefresh();
          } catch (e) {
            vscode.window.showErrorMessage(`Failed to kill agent: ${String(e)}`);
          }
        }
      },
    ),

    vscode.commands.registerCommand(
      'bernstein.showAgentOutput',
      (item: AgentItem) => {
        outputManager.show(item.agent.id);
      },
    ),

    vscode.commands.registerCommand(
      'bernstein.cancelTask',
      async (item: TaskItem) => {
        const answer = await vscode.window.showWarningMessage(
          `Cancel task "${item.task.title}"?`,
          { modal: true },
          'Cancel Task',
        );
        if (answer === 'Cancel Task') {
          try {
            await client.cancelTask(item.task.id);
            vscode.window.showInformationMessage(`Task "${item.task.title}" cancelled.`);
            onRefresh();
          } catch (e) {
            vscode.window.showErrorMessage(`Failed to cancel task: ${String(e)}`);
          }
        }
      },
    ),

    vscode.commands.registerCommand(
      'bernstein.openTask',
      (item: TaskItem) => {
        if (item.task.assigned_agent) {
          outputManager.show(item.task.assigned_agent);
        }
      },
    ),

    vscode.commands.registerCommand(
      'bernstein.prioritizeTask',
      async (item: TaskItem) => {
        try {
          await client.prioritizeTask(item.task.id);
          vscode.window.showInformationMessage(`Task "${item.task.title}" moved to top of queue.`);
          onRefresh();
        } catch (e) {
          vscode.window.showErrorMessage(`Failed to prioritize task: ${String(e)}`);
        }
      },
    ),

    vscode.commands.registerCommand(
      'bernstein.inspectAgent',
      (item: AgentItem) => {
        const a = item.agent;
        const runtime =
          a.runtime_s > 60
            ? `${Math.floor(a.runtime_s / 60)}m`
            : `${a.runtime_s}s`;
        const taskList = a.tasks?.length
          ? a.tasks.map((t) => t.title).join(', ')
          : 'No tasks';
        const msg = [
          `Agent: ${a.id}`,
          `Role: ${a.role}`,
          `Model: ${a.model ?? 'unknown'}`,
          `Status: ${a.status}`,
          `Runtime: ${runtime}`,
          `Cost: $${a.cost_usd.toFixed(4)}`,
          `Tasks: ${taskList}`,
        ].join(' | ');
        void vscode.window.showInformationMessage(msg);
      },
    ),

  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd packages/vscode && npx jest src/__tests__/commands.test.ts --no-coverage
```

Expected: All tests PASS

- [ ] **Step 5: Add `bernstein.inspectAgent` to `package.json`**

In `packages/vscode/package.json`, make two additions:

**a) Add to `contributes.commands` array** (after `bernstein.openTask`):
```json
{ "command": "bernstein.inspectAgent", "title": "Bernstein: Inspect Agent", "icon": "$(info)" }
```

**b) Add to `contributes.menus["view/item/context"]` array** (after the last `bernstein.showAgentOutput` entry):
```json
{ "command": "bernstein.inspectAgent", "when": "view == bernstein.agents", "group": "1_agent_actions@3" }
```

The full `view/item/context` array should now be:
```json
"view/item/context": [
  { "command": "bernstein.killAgent", "when": "view == bernstein.agents && viewItem == agent.active", "group": "inline" },
  { "command": "bernstein.showAgentOutput", "when": "view == bernstein.agents && viewItem == agent.active", "group": "inline" },
  { "command": "bernstein.killAgent", "when": "view == bernstein.agents && viewItem == agent.active", "group": "1_agent_actions@1" },
  { "command": "bernstein.showAgentOutput", "when": "view == bernstein.agents && viewItem == agent.active", "group": "1_agent_actions@2" },
  { "command": "bernstein.inspectAgent", "when": "view == bernstein.agents", "group": "1_agent_actions@3" },
  { "command": "bernstein.prioritizeTask", "when": "view == bernstein.tasks && viewItem =~ /^task\\.(open|claimed|in_progress)$/", "group": "1_task_actions@1" },
  { "command": "bernstein.cancelTask", "when": "view == bernstein.tasks && viewItem =~ /^task\\.(open|claimed|in_progress)$/", "group": "1_task_actions@2" }
]
```

- [ ] **Step 6: Verify JSON is valid**

```bash
cd packages/vscode && node -e "require('./package.json'); console.log('JSON valid')"
```

Expected: `JSON valid`

- [ ] **Step 7: Run type check**

```bash
cd packages/vscode && npx tsc --noEmit
```

Expected: no output (no errors)

- [ ] **Step 8: Run all tests**

```bash
cd packages/vscode && npm test
```

Expected: All test suites PASS

- [ ] **Step 9: Commit**

```bash
cd packages/vscode
git add src/commands.ts src/__tests__/commands.test.ts package.json
git commit -m "feat(ext): add bernstein.inspectAgent command with test coverage"
```

---

## Task 3: Fix PUBLISH.md token names

**Files:**
- Modify: `packages/vscode/PUBLISH.md`

- [ ] **Step 1: Fix `VSCE_PAT` → `VS_MARKETPLACE_TOKEN` and `OVSX_PAT` → `OPEN_VSX_TOKEN`**

Open `packages/vscode/PUBLISH.md`. In the "Manual Publishing (if needed)" section, replace:

```bash
# Publish to VS Code Marketplace
VSCE_PAT=YOUR_TOKEN npm run publish:vscode

# Publish to Open VSX
OVSX_PAT=YOUR_TOKEN npm run publish:ovsx
```

with:

```bash
# Publish to VS Code Marketplace
VS_MARKETPLACE_TOKEN=YOUR_TOKEN npm run publish:vscode

# Publish to Open VSX
OPEN_VSX_TOKEN=YOUR_TOKEN npm run publish:ovsx
```

- [ ] **Step 2: Verify no stale token names remain**

```bash
grep -n "VSCE_PAT\|OVSX_PAT" packages/vscode/PUBLISH.md
```

Expected: no output (no matches)

- [ ] **Step 3: Commit**

```bash
git add packages/vscode/PUBLISH.md
git commit -m "docs(ext): fix token names in PUBLISH.md (VS_MARKETPLACE_TOKEN, OPEN_VSX_TOKEN)"
```

---

## Task 4: Verify extension is publishable

**Files:** None modified — verification only.

- [ ] **Step 1: Full build and package (dry run)**

```bash
cd packages/vscode && npm run compile && npx vsce package --no-dependencies
```

Expected:
- Build succeeds with no errors
- A `bernstein-0.1.0.vsix` file is created

- [ ] **Step 2: Check package size**

```bash
ls -lh packages/vscode/bernstein-*.vsix
```

Expected: File size is < 1 MB (should be ~20-800KB)

- [ ] **Step 3: Inspect package contents**

```bash
cd packages/vscode && npx vsce ls --no-dependencies 2>/dev/null | head -30
```

Expected: Only `dist/`, `media/`, `README.md`, `CHANGELOG.md`, `package.json` — no `src/`, no `node_modules/`, no `*.map` files

- [ ] **Step 4: Clean up VSIX artifact**

```bash
rm packages/vscode/bernstein-*.vsix
```

- [ ] **Step 5: Commit design doc and plan**

```bash
git add docs/superpowers/specs/2026-03-29-extension-publish-ux-design.md \
        docs/superpowers/plans/2026-03-29-extension-publish-ux.md
git commit -m "docs: add extension publish pipeline design spec and implementation plan"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| GAP-1: `bernstein.openTask` in package.json | Task 1 |
| GAP-2: `bernstein.inspectAgent` command + test | Task 2 |
| GAP-3: PUBLISH.md token name inconsistency | Task 3 |
| GAP-4: Extension size verification | Task 4 |
| Right-click agent → Kill / Inspect / Show Logs | Task 2, Step 5 adds Inspect to context menu |
| CI/CD pipeline completeness | Pre-existing, verified in design spec |

**No placeholders** — all code is concrete and complete.

**Type consistency** — `AgentItem`, `TaskItem`, `BernsteinAgent` types are used consistently throughout. `item.agent` access matches `AgentItem` interface defined in `AgentTreeProvider.ts`.

---

## Mark Task Complete

After all tasks pass, mark done on the task server:

```bash
curl -s --retry 3 --retry-delay 2 --retry-all-errors \
  -X POST http://127.0.0.1:8052/tasks/43ff11d8dd00/complete \
  -H "Content-Type: application/json" \
  -d '{"result_summary": "Completed: Extension Publish Pipeline + UX Polish — fixed bernstein.openTask declaration, added bernstein.inspectAgent with tests, fixed PUBLISH.md token names, verified package size"}'
```
