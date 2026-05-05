# Feature contract

A plan step can carry an immutable list of features. Each feature has
an id, a description, an acceptance check, and a `passes` flag. Agents
may flip `passes: true` only when the declared acceptance check
actually exits zero. The list is hash-anchored in the audit chain so
agents cannot quietly add, remove, or weaken entries.

## Why it exists

Two failure modes show up in long-running self-evolution runs:

1. **Premature victory** — agent completes 6 of 10 features and calls
   `POST /tasks/{id}/complete`, declaring the task done.
2. **Test deletion** — agent "passes" by weakening or removing the
   failing test rather than fixing the code.

The feature contract is the immutable spec the agent reads but cannot
meaningfully game, and the per-feature pass/fail board that survives
across sessions.

## How to use it

Add a `features:` block to a step in your plan YAML:

```yaml
# plans/jwt-auth.yaml
stages:
  - name: backend
    steps:
      - role: backend
        goal: "Add JWT auth with refresh tokens"
        features:
          - id: jwt-issue
            description: "POST /auth/login returns a JWT"
            acceptance_steps:
              - "Login with valid creds"
            acceptance_check: "pytest tests/auth/test_login.py::test_jwt_issued"
          - id: jwt-refresh
            description: "POST /auth/refresh exchanges a refresh token for a new JWT"
            acceptance_steps:
              - "Refresh with a valid refresh token"
            acceptance_check: "pytest tests/auth/test_refresh.py"
          - id: revocation
            description: "Revoked refresh tokens cannot be reused"
            acceptance_check: "pytest tests/auth/test_revocation.py"
```

Run the plan as usual. Inspect feature state at any time:

```bash
# Per-feature pass/fail board (exits non-zero if any feature is pending or failed)
bernstein contract status

# Force-run every acceptance check now
bernstein contract verify
```

When an agent calls `POST /tasks/{id}/complete` while a feature is
still `passes: false`, the server replies `400` and lists the failing
ids. Pass `--allow-partial` (operator-only flag) to override.

## How tamper-detection works

The contract is persisted at `.sdd/contract/features.json`. Its
canonical sha256 is stored in the HMAC-chained audit log. Any
in-place edit by an agent is detected on the next chain validation.

The janitor re-runs each `acceptance_check` post-merge. If a
previously-passing check becomes a no-op (test file deleted, assertion
weakened to `assert True`), the janitor flags it as
`acceptance_check_decay`.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `features.completion_blocks_on_failure` | `true` | Reject `complete` if any feature is `passes: false`. |
| `features.allow_partial_flag_required` | `true` | Only the operator-level `--allow-partial` overrides. |
| `features.janitor_recheck_interval_s` | `0` (every drain) | How often the janitor re-runs acceptance checks. |

## Limitations

- The operator authors `acceptance_steps` and `acceptance_check`. No
  LLM-generated suggestions in v1.
- No cross-project feature library. Contracts live with the plan.
- CLI table output only — no visual board UI.
- Acceptance checks run as shell commands; supply them with care
  (the existing command allowlist still applies).

## Related

- Source: `src/bernstein/core/planning/feature_contract.py`
- Audit hook: `src/bernstein/core/security/audit.py`
- Janitor integration: `src/bernstein/core/quality/janitor.py`
- CLI: `bernstein contract status`, `bernstein contract verify`
- PR #997, ticket `2026-04-30-feat-feature-list-immutable-contract.md`
