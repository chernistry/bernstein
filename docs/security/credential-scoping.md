# Credential scoping (default-on)

Bernstein spawns each leaf-agent subprocess with a **filtered**
credential view: only the env vars the policy permits for that agent
or role are forwarded; everything else in the orchestrator's
environment (database URLs, CI tokens, billing keys, internal
secrets) is stripped before the child starts.

The policy is on by default in fresh installs and can be opted out at
the file, config, or env-var layer.

| Layer | Source |
|-------|--------|
| Bundled fallback | `examples/credential-policies/default.yaml` |
| Project override | `.bernstein/credential_policy.yaml` (also `.yml`) |
| Legacy path | `.sdd/config/credential_scopes.yaml` (also `.yml`) |
| Env override | `BERNSTEIN_CREDENTIAL_POLICY_PATH=/abs/path.yaml` |
| Opt-out | `BERNSTEIN_DISABLE_CREDENTIAL_SCOPING=1` |

Source: `src/bernstein/core/credential_scoping.py`.

---

## Resolution order

`resolve_default_policy()` runs once at orchestrator startup:

1. `BERNSTEIN_DISABLE_CREDENTIAL_SCOPING` truthy - return an empty
   (disabled) policy and log one INFO line.
2. `BERNSTEIN_CREDENTIAL_POLICY_PATH` set to a readable file - load
   that file. Errors propagate so misconfigurations are loud.
3. First existing entry in `DEFAULT_POLICY_PATHS` - load it. The chain:
    1. `.bernstein/credential_policy.yaml`
    2. `.bernstein/credential_policy.yml`
    3. `.sdd/config/credential_scopes.yaml`
    4. `.sdd/config/credential_scopes.yml`
    5. `credential_policy.yaml`
    6. `examples/credential-policies/default.yaml` (bundled)
4. Nothing found - return an empty policy and log one WARN line so
   operators know they are running unscoped.

The bundled `default.yaml` ships with `enabled: true`, the four
provider keys every leaf-agent backend recognises, plus
`GH_TOKEN` / `GITHUB_TOKEN` so adapters that push branches keep
working. Fresh checkouts therefore get fail-closed scoping out of
the box; existing deployments that want to keep the legacy unscoped
path delete or rename the bundled file (or set the env-var opt-out).

---

## Schema

```yaml
# .bernstein/credential_policy.yaml
enabled: true

# Spell-check surface - every credential env-var name any rule below
# may grant. The loader rejects rules that reference a key not in
# this list, which catches typos like ANTHORPIC_API_KEY at parse time.
known_keys:
  - OPENAI_API_KEY
  - ANTHROPIC_API_KEY
  - GEMINI_API_KEY
  - GH_TOKEN
  - GITHUB_TOKEN

# Per-agent rules. Exact id, or glob pattern such as "backend-*".
agents:
  backend-001:
    - ANTHROPIC_API_KEY
    - GH_TOKEN
  researcher-*:
    - OPENAI_API_KEY

# Per-role fallback. Consulted when no agent rule matches.
roles:
  backend:
    - ANTHROPIC_API_KEY
    - GH_TOKEN
  researcher:
    - OPENAI_API_KEY
```

Matching order: exact `agents` entry → glob `agents` entry (sorted
deterministically) → `roles[role]` → fail-closed.

---

## Fail-closed semantics

Once `enabled: true`:

- Agents not covered by any rule raise `AgentNotScopedError` at spawn
  time. The orchestrator surfaces a clear remediation message naming
  the agent id, the role, and the rule kinds the operator can add.
- An adapter that requests an env-var name not declared in
  `known_keys` raises `UnknownCredentialKeyError`. This is a
  configuration bug, not a permission denial, so it raises rather
  than silently dropping.
- The empty fallback policy (when nothing is found) is a no-op:
  callers receive whatever keys they request. The startup log line
  carries the candidate paths so an operator can grep `agent.log`
  and learn exactly where the loader looked.

The two error types are subclasses of `CredentialScopingError`, so a
caller that wants to handle both at once can catch the parent.

---

## Adapter integration

Adapters obtain their permitted env-var subset through
`build_filtered_env`. The hot-path call is:

```python
from bernstein.adapters.env_isolation import build_filtered_env

env = build_filtered_env(
    extra_keys=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"],
    agent_id=session_id,
    role="backend",
)
```

The filter intersects `extra_keys` with the policy-allowed set and
returns only the keys the agent is entitled to. The filtered env is
what the subprocess inherits - the orchestrator's other secrets never
cross the spawn boundary.

For ad-hoc paths that do not go through `build_filtered_env`,
`scoped_credential_keys()` exposes the same logic directly.

---

## Storage-sink keys are never forwarded

Cloud storage keys (`AWS_ACCESS_KEY_ID`, `R2_*`, `GOOGLE_APPLICATION_CREDENTIALS`,
`AZURE_STORAGE_*`, etc.) are listed in `STORAGE_CREDENTIAL_ENV_VARS`
in `core.storage.credential_scoping` and stripped from every spawned
env regardless of the per-agent policy. A compromised agent therefore
cannot exfiltrate the orchestrator's long-lived cloud keys even when
the agent's role lists `AWS_ACCESS_KEY_ID` in `known_keys` by mistake
- the storage scrubber runs after the per-agent filter.

`list_storage_credential_env_vars()` exposes the list so audit and
documentation can enumerate the current surface.

---

## Diagnostics

`bernstein doctor scoping` (and the underlying
`explain_policy_for_agent()` helper) returns a structured snapshot
showing:

- whether the policy is enabled,
- the resolution path that won (or the WARN line when nothing was
  found),
- the keys an example agent / role would receive,
- the keys the orchestrator process actually has loaded.

The diff between "would receive" and "has loaded" is the operator
checklist - keys that show up in *would* but not in *has* are
declared in the policy but missing from the host environment, and
keys in *has* but not in *would* are correctly being stripped.

---

## Related

- Source: `src/bernstein/core/credential_scoping.py`,
  `src/bernstein/core/storage/credential_scoping.py`,
  `src/bernstein/adapters/env_isolation.py`
- Bundled default: `examples/credential-policies/default.yaml`
- [Secrets and credentials](../operations/secrets.md) - where the
  underlying tokens live (vault, secrets-manager, env vars)
- [Env isolation](../operations/env-isolation.md) - sibling page
  covering the env-var allowlist mechanics
- [Capability matrix](capability-matrix.md) - the role-to-tool
  binding that pairs with the role-to-credential binding here
