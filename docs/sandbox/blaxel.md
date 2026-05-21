# Blaxel sandbox backend

Bernstein's `blaxel` sandbox backend talks to the Blaxel control plane
(<https://blaxel.ai>) over its public REST API and conforms to the
`SandboxBackend` protocol exposed by `bernstein.core.sandbox`.

## Module

`src/bernstein/core/sandbox/backends/blaxel.py` -
class `BlaxelSandboxBackend`, registered under the name `blaxel` in
`bernstein.core.sandbox.registry`.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `BLAXEL_API_KEY` | yes | Bearer token issued in Workspace Settings -> API Keys. |
| `BLAXEL_WORKSPACE` | yes | Workspace slug that owns the sandboxes. |
| `BLAXEL_API_URL` | no | Override of the API root. Defaults to `https://api.blaxel.ai/v0`. |

When any required variable is missing the backend raises
`SandboxCredentialError` from `bernstein.core.sandbox.backends._http_helpers`
naming the missing variable.

## Capabilities

`FILE_RW`, `EXEC`, `NETWORK`, `PERSISTENT_VOLUMES`.

`SNAPSHOT` is **not** advertised because the public Blaxel REST API
does not expose a snapshot/restore primitive at time of writing.
`backend.snapshot()` and `backend.resume()` raise `NotImplementedError`
with a pointer to the vendor changelog.

## Selecting the backend

```yaml
sandbox:
  backend: blaxel
  options:
    runtime: python:3.13
    region: us-east-1
```

## Honest limitations

- **No exec streaming.** The Blaxel REST endpoint returns the final
  combined `stdout`/`stderr` of the command after the process exits,
  rather than streaming over WebSockets. Long-running interactive
  workloads that need tail-style log streaming should use the
  `worktree` or `docker` backends.
- **Snapshots not supported.** Provider does not expose
  snapshot/resume; persistent state lives in the workspace volume
  attached to the sandbox.

## Integration tests

Live integration tests are gated by `CI_BLAXEL_TEST=1` plus the
credentials above. Without them the test in
`tests/integration/sandbox/test_blaxel_backend.py` skips cleanly.
