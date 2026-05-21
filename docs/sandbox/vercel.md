# Vercel sandbox backend

Bernstein's `vercel` sandbox backend talks to the Vercel Sandbox API
(<https://vercel.com/docs/sandbox>) and conforms to the
`SandboxBackend` protocol.

## Module

`src/bernstein/core/sandbox/backends/vercel.py` -
class `VercelSandboxBackend`, registered under the name `vercel` in
`bernstein.core.sandbox.registry`.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `VERCEL_TOKEN` | yes | Personal/team API token (`https://vercel.com/account/tokens`). |
| `VERCEL_TEAM_ID` | conditional | Required for tokens scoped to a team. Forwarded as `?teamId=` on every request. |
| `VERCEL_API_URL` | no | API root override. Defaults to `https://api.vercel.com`. |

## Capabilities

`FILE_RW`, `EXEC`, `NETWORK`. `SNAPSHOT` is **not** declared because
the Vercel Sandbox API does not currently expose a snapshot/restore
primitive.

## Selecting the backend

```yaml
sandbox:
  backend: vercel
  options:
    runtime: node22
    region: iad1
```

## Honest limitations

- **No exec streaming on the synchronous endpoint.** The Vercel
  Sandbox HTTP API returns the buffered `stdout`/`stderr` after the
  command exits. For interactive workloads (tail-style log
  streaming), use the `worktree` or `docker` backends.
- **No stdin on the sync exec route.** Passing `stdin=` raises
  `NotImplementedError`.
- **Snapshots not supported.** Persist state via Vercel-managed
  storage (KV / Blob) rather than relying on session snapshotting.

## Integration tests

Live integration tests are gated by `CI_VERCEL_TEST=1` plus
`VERCEL_TOKEN`. Without them the test in
`tests/integration/sandbox/test_vercel_backend.py` skips cleanly.
