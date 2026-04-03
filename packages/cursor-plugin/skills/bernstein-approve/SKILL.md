---
name: bernstein-approve
description: >
  Review and approve/reject pending tasks or plans in Bernstein.
  Use when the user asks about approvals, wants to review agent work,
  or needs to approve/reject a plan before execution begins.
---

# Bernstein Approvals

Review pending approvals and plans, then approve or reject them.

## When to Use

- User asks "any pending approvals?" or "what needs my review?"
- User wants to approve or reject a completed task
- User wants to review and approve/reject a plan before agents execute it
- User says "approve that" or "reject task X"

## Instructions

### List pending approvals

1. Run `scripts/approvals.sh list` to fetch pending approvals and plans.
2. Present each item with its diff summary and test results:

```
## Pending Approvals

### Task TASK-042: Fix auth middleware
**Agent:** claude-backend-01 | **Role:** backend
**Files changed:** src/auth/middleware.py (+42 -8)
**Tests:** 14/14 passing

[Approve] [Reject]
```

### Approve or reject

3. When the user decides:
   - Approve: `scripts/approvals.sh approve <task_id> "reason"`
   - Reject: `scripts/approvals.sh reject <task_id> "reason"`

4. For **plans** (multi-task proposals):
   - List plans: `scripts/approvals.sh plans`
   - Approve plan: `scripts/approvals.sh approve-plan <plan_id>`
   - Reject plan: `scripts/approvals.sh reject-plan <plan_id>`

### After approval

5. Confirm the action and note what happens next:
   - Approved tasks → agent commits and merges
   - Approved plans → planned tasks promoted to open, agents start picking them up
   - Rejected → task goes back to open for retry, or gets cancelled
