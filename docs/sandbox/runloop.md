# Runloop sandbox backend

Bernstein's `runloop` sandbox backend talks to the Runloop REST API
(<https://runloop.ai>) and conforms to the `SandboxBackend` protocol.

## Module

`src/bernstein/core/sandbox/backends/runloop.py` —
class `RunloopSandboxBackend`, registered under the name `runloop` in
`bernstein.core.sandbox.registry`.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `RUNLOOP_API_KEY` | yes | Bearer token from the Runloop Dashboard. |
| `RUNLOOP_API_URL` | no | API root override. Defaults to `https://api.runloop.ai/v1`. |
| `RUNLOOP_PROJECT_ID` | no | Default project id forwarded as `project_id` on devbox creation. |

## Capabilities

`FILE_RW`, `EXEC`, `NETWORK`, `SNAPSHOT`. Runloop devboxes can be
snapshotted via `POST /devboxes/{id}/snapshot_disk` and resumed via
`POST /devboxes` with `snapshot_id`.

## Selecting the backend

```yaml
sandbox:
  backend: runloop
  options:
    blueprint: runloop/blueprint-python
    project_id: prj-bernstein
```

## Honest limitations

- **Synchronous exec only.** This backend uses
  `POST /devboxes/{id}/execute_sync` which buffers stdout/stderr until
  the command exits. Long-running interactive workloads should use
  Runloop's WebSocket exec channel — routing it through the unary
  `SandboxSession.exec` contract is tracked as a follow-up.
- **No stdin injection.** Passing `stdin=` raises
  `NotImplementedError`.

## Integration tests

Live integration tests are gated by `CI_RUNLOOP_TEST=1` plus
`RUNLOOP_API_KEY`. Without them the test in
`tests/integration/sandbox/test_runloop_backend.py` skips cleanly.
