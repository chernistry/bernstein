# Bernstein MCP server

Bernstein exposes its orchestration layer as MCP tools so any MCP client
(Cursor, Claude Code, Cline, Windsurf, and others) can drive multi-agent work
through Bernstein. This page describes the protocol surface and how to point a
client at the server. For the per-tier tool catalogue see
[`tool_tiers.md`](tool_tiers.md); for the full surface audit see
[`server-audit-2026.md`](server-audit-2026.md).

## Transports

| Transport | Command | Use when |
|-----------|---------|----------|
| stdio (default) | `bernstein mcp` | Local IDE integration. |
| SSE | `bernstein mcp --transport http` | Remote/web integration. |
| Streamable HTTP | served on `/mcp` | Remote integration with session management and cancellation. |

The streamable HTTP transport binds to loopback by default. Binding to a
public interface requires a bearer token (see Auth) and is otherwise refused
at startup.

## Auth

| Mode | How | Notes |
|------|-----|-------|
| Anonymous | default on loopback | Allowed only on `127.0.0.1` / `localhost` / `::1`. |
| Static bearer | `BERNSTEIN_MCP_TOKEN` (or `BERNSTEIN_MCP_AUTH_TOKEN`) | Constant-time check; required on non-loopback binds. |

OAuth-2 PKCE and OIDC federation are not yet implemented. The capability card
advertises them under `auth.planned` so a client sees the gap as acknowledged.

## Capability cards

Beyond the static `capabilities` object on `initialize`, the server publishes
a runtime capability card describing how it is actually running: reachable
transports, configured auth modes, the active tool tier, the cost-meter
state, and the targeted spec revision. The card is built from live process
state on each read, so it reflects the current configuration without a
restart.

The card is available two ways:

- as the `bernstein://capability` MCP resource (read it with the client's
  resource API);
- under the `capabilityCard` key on the streamable HTTP transport's
  `initialize` result.

## Per-call cost-meter envelope

Every tool response is wrapped in a uniform envelope so observability is
consistent across transports:

```json
{
  "result": { "status": "ok" },
  "_meter": {
    "tool": "bernstein_health",
    "call_id": "b1c2...",
    "latency_ms": 12.4,
    "cost_usd": 0.0,
    "ok": true,
    "ts": "2026-05-20T10:11:12.345Z"
  }
}
```

`cost_usd` is best-effort: the MCP server proxies to the task server and does
not itself spend model tokens, so the per-call figure is `0.0` unless a
handler attaches a cost. The field exists so the envelope shape is stable.

To get the bare tool payload (the historical shape), disable the meter:

```bash
export BERNSTEIN_MCP_COST_METER=0
```

## Streaming cancel with partial-result preservation

On the streamable HTTP transport, each `tools/call` runs as a cancellable
task tracked by its JSON-RPC id. A client cancels an in-flight call by sending
a `notifications/cancelled` notification carrying that `requestId`. The
originating call then returns the work done before the stop rather than a bare
error:

```json
{
  "content": [{ "type": "text", "text": "{\"status\": \"running\", ...}" }],
  "cancelled": true,
  "partial": ["{\"status\": \"running\", \"tool\": \"bernstein_run\"}"],
  "_meter": { "tool": "bernstein_run", "ok": false, "...": "..." }
}
```

`isError` is not set: a cancel is a client-initiated stop, not a tool failure.
Cancelling an unknown or already-settled id is a no-op.

## Worked example: pointing a host at the server

1. Start the server over the streamable HTTP transport on loopback:

   ```bash
   export BERNSTEIN_MCP_TOKEN=dev-token
   bernstein mcp --transport http --host 127.0.0.1 --port 8053
   ```

2. Initialize and read the capability card:

   ```bash
   curl -s http://127.0.0.1:8053/mcp \
     -H "Authorization: Bearer dev-token" \
     -H "content-type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"example"}}}'
   ```

   The `result.capabilityCard` shows the active tier, auth modes, and
   transports the client can use.

3. Call a tool. The response carries the cost-meter envelope:

   ```bash
   curl -s http://127.0.0.1:8053/mcp \
     -H "Authorization: Bearer dev-token" \
     -H "content-type: application/json" \
     -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"bernstein_status","arguments":{}}}'
   ```

4. Cancel a long-running call by its id (in a second request, while the call
   is in flight):

   ```bash
   curl -s http://127.0.0.1:8053/mcp \
     -H "Authorization: Bearer dev-token" \
     -H "content-type: application/json" \
     -d '{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2}}'
   ```

   The original call returns `cancelled: true` with its preserved `partial`
   output.
