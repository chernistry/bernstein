---
name: bernstein-plan
description: >
  Create and manage multi-step execution plans in Bernstein. Plans decompose
  complex goals into stages with dependencies. Use when the user wants to
  plan a complex feature, break down a large task, or review an execution plan
  before agents start working.
---

# Bernstein Plan Mode

Create structured execution plans that get human approval before agents start.

## When to Use

- User describes a complex feature: "implement user authentication with OAuth"
- User wants to break down a large task into stages
- User says "plan this out" or "create a plan for..."
- User wants to review what agents will do before they start

## Instructions

### Creating a plan

1. Analyze the user's request and decompose it into stages and tasks.
2. Write a plan YAML file using this structure:

```yaml
name: "{descriptive plan name}"
description: "{what this plan achieves}"
stages:
  - name: foundation
    steps:
      - goal: "Create database models for user and session"
        role: backend
        scope: small
        complexity: low
      - goal: "Add migration scripts"
        role: backend
        scope: tiny

  - name: implementation
    depends_on: [foundation]
    steps:
      - goal: "Implement OAuth2 flow with Google provider"
        role: backend
        scope: medium
        complexity: medium
      - goal: "Create login/signup UI components"
        role: frontend
        scope: medium

  - name: verification
    depends_on: [implementation]
    steps:
      - goal: "Write integration tests for auth flow"
        role: qa
        scope: medium
      - goal: "Security review of token handling"
        role: security
        scope: small
```

3. Save the plan to `plans/{plan-name}.yaml` in the project root.
4. Tell the user to execute it: `bernstein run plans/{plan-name}.yaml`

### Or submit via API

5. Run `scripts/plan.sh submit plans/{plan-name}.yaml` to submit for approval.
6. The plan enters `pending` state — use `/bernstein-approve` to review and approve.
7. Once approved, planned tasks promote to `open` and agents start picking them up.

### Reviewing plans

8. Run `scripts/plan.sh list` to see all plans and their status.
9. Show the plan with stages, dependencies, and estimated cost/time.

## Tips

- Keep stages to 2-5 tasks each
- Use `depends_on` to enforce ordering (foundation before implementation)
- Assign appropriate roles: backend, frontend, qa, security, devops, docs
- Mark risky tasks with `complexity: high` — they'll get more capable models
- Foundation stages should be `tiny` or `small` scope
