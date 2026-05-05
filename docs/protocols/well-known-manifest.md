# Static service manifest (`.well-known/agent.json` + `llms.txt`)

Bernstein's task server publishes two machine-readable manifests so
external agents (Claude Code, Codex, third-party orchestrators) can
discover its endpoints, auth scheme, and task-orchestration
capabilities without hand configuration.

| URL | Format | Purpose |
|---|---|---|
| `GET /.well-known/agent.json` | A2A-compliant JSON | Structured agent card with auth, endpoints, version |
| `GET /llms.txt` | markdown | Human + LLM-friendly summary of the same |

Both endpoints are unauthenticated and listed in the auth-middleware
whitelist; they expose the public surface only.

## Why it exists

Before this, an external agent talking to Bernstein had to be
hand-configured: someone had to know "task server is on 8052,
endpoints are POST /tasks, etc." Serving a static manifest closes the
loop and makes Bernstein a first-class platform other agents can
discover.

## How to use it

Hit either endpoint:

```bash
curl http://127.0.0.1:8052/.well-known/agent.json
curl http://127.0.0.1:8052/llms.txt
```

Sample `agent.json` (truncated):

```json
{
  "schema_version": "0.1.0",
  "name": "bernstein-task-server",
  "version": "1.9.4",
  "auth": {
    "scheme": "bearer",
    "token_endpoint": null
  },
  "endpoints": {
    "tasks_create":   "POST /tasks",
    "tasks_list":     "GET /tasks",
    "tasks_complete": "POST /tasks/{id}/complete",
    "bulletin_post":  "POST /bulletin",
    "bulletin_read":  "GET /bulletin"
  }
}
```

`llms.txt` is the same information rendered as markdown for an LLM to
parse, plus a short prose description.

The contents are rendered from the templates `templates/well_known/agent.json.j2`
and `templates/well_known/llms.txt.j2` against the running server's
version + endpoint list at startup.

## Configuration

There are no user-facing knobs. The endpoints are always served and
always public. To customise the manifest content, edit the Jinja
templates under `templates/well_known/` and rebuild.

## Limitations

- One global manifest per server. No per-tenant customisation in v1.
- Plugin / adapter manifests are **not** aggregated — only the
  task-server's own surface is published.
- The manifest is static at boot. Endpoints added by hot-loaded
  plugins after startup do not appear until restart.
- Hosting at `bernstein.dev` is not part of this — the endpoints live
  on the local task server.

## Related

- Source: `src/bernstein/core/routes/well_known.py`
- Templates: `templates/well_known/`
- Auth middleware: `src/bernstein/core/security/auth_middleware.py`
- A2A schema: parsed by `claude_agent_card.py`
- PR #1004, ticket `2026-04-30-feat-static-service-manifest.md`
