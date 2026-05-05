# Daytona sandbox backend

Bernstein's `daytona` sandbox backend talks to the Daytona REST API
(<https://daytona.io>, <https://github.com/daytonaio/daytona>) and
conforms to the `SandboxBackend` protocol.

## Module

`src/bernstein/core/sandbox/backends/daytona.py` —
class `DaytonaSandboxBackend`, registered under the name `daytona` in
`bernstein.core.sandbox.registry`.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DAYTONA_API_KEY` | yes | Personal access token (Daytona Dashboard -> Settings -> API Keys). |
| `DAYTONA_API_URL` | no | API root override. Defaults to `https://app.daytona.io/api`. |
| `DAYTONA_TARGET` | no | Region/target id (`us`, `eu`, ...). Forwarded as the `target` field on creation. |
| `DAYTONA_ORG_ID` | no | Organisation id, sent as the `X-Daytona-Organization-ID` header for multi-org accounts. |

## Capabilities

`FILE_RW`, `EXEC`, `NETWORK`, `SNAPSHOT`. The Daytona API supports
snapshot / restore, so `backend.snapshot()` and `backend.resume()`
are wired through.

## Selecting the backend

```yaml
sandbox:
  backend: daytona
  options:
    image: daytonaio/sandbox:latest
    target: us
    cpu: 2
    memory: 4Gi
```

## Honest limitations

- **Stdin not supported on the REST exec endpoint.** Passing `stdin=`
  raises `NotImplementedError`. For interactive workloads use the
  Daytona WebSocket exec channel directly; routing it through the
  Bernstein protocol is tracked as a follow-up because
  `SandboxSession.exec` is currently unary-response.
- **No exec streaming.** The endpoint returns the buffered
  `stdout`/`stderr` after the command exits.

## Integration tests

Live integration tests are gated by `CI_DAYTONA_TEST=1` plus
`DAYTONA_API_KEY`. Without them the test in
`tests/integration/sandbox/test_daytona_backend.py` skips cleanly.
