"""Static service manifest routes — A2A agent card and llms.txt summary.

External agents (Claude Code, Codex, third-party orchestrators) discover the
Bernstein task API by fetching ``/.well-known/agent.json`` (A2A-compliant
JSON card) or ``/llms.txt`` (markdown summary).  Both endpoints derive from
a single in-module ``_ENDPOINTS`` table so the markdown summary cannot drift
from the structured manifest — the regression test in
``tests/unit/test_well_known.py`` enforces that every entry in the table is
mentioned in the rendered llms.txt body.

Both routes are unauthenticated; they live in ``AUTH_PUBLIC_PATHS`` so any
network caller can read them without provisioning a token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from bernstein import __version__ as _BERNSTEIN_VERSION

router = APIRouter()

_AGENT_NAME = "bernstein"
_AGENT_DESCRIPTION = (
    "Bernstein orchestrates short-lived CLI coding agents (Claude Code, "
    "Codex, Gemini CLI, Aider, ...) against a file-based task store.  "
    "Clients submit tasks, query status, and post cross-agent bulletins "
    "via the documented endpoints below."
)
_PROTOCOL_VERSION = "0.2"
_DEFAULT_BASE_URL = "http://127.0.0.1:8052"
_DOCS_URL = "https://github.com/sipyourdrink-ltd/bernstein"


@dataclass(frozen=True, slots=True)
class _Endpoint:
    """Single documented endpoint in the manifest."""

    method: str
    path: str
    summary: str


_ENDPOINTS: tuple[_Endpoint, ...] = (
    _Endpoint("POST", "/tasks", "Create a new task in the backlog."),
    _Endpoint("GET", "/tasks", "List tasks (filter via ?status=open|claimed|done)."),
    _Endpoint("GET", "/tasks/{id}", "Fetch a single task by id."),
    _Endpoint("POST", "/tasks/{id}/complete", "Mark task done with a result summary."),
    _Endpoint("POST", "/tasks/{id}/fail", "Mark task failed with an error reason."),
    _Endpoint("POST", "/tasks/{id}/progress", "Report partial progress (files, tests, errors)."),
    _Endpoint("POST", "/bulletin", "Post a finding or blocker visible to other agents."),
    _Endpoint("GET", "/bulletin", "Read recent bulletins (filter via ?since=ts)."),
    _Endpoint("GET", "/status", "Server-side dashboard summary."),
    _Endpoint("GET", "/health", "Liveness probe."),
    _Endpoint("GET", "/health/ready", "Readiness probe."),
)

_SKILLS: tuple[dict[str, object], ...] = (
    {
        "id": "task-orchestration",
        "name": "Task orchestration",
        "description": "Submit goals, watch their progress, and react to terminal state.",
        "tags": ["tasks", "orchestration"],
    },
    {
        "id": "agent-bulletin",
        "name": "Cross-agent bulletin",
        "description": "Broadcast findings and blockers to peer agents.",
        "tags": ["bulletin", "messaging"],
    },
)


def _agent_card_payload(base_url: str = _DEFAULT_BASE_URL) -> dict[str, Any]:
    """Build the A2A agent-card dict served at /.well-known/agent.json.

    Args:
        base_url: Public base URL of the task server.

    Returns:
        JSON-serialisable dict accepted by ``parse_agent_card``.
    """
    return {
        "name": _AGENT_NAME,
        "description": _AGENT_DESCRIPTION,
        "version": _BERNSTEIN_VERSION,
        "protocolVersion": _PROTOCOL_VERSION,
        "url": base_url,
        "documentationUrl": _DOCS_URL,
        "capabilities": [
            {"name": "task-crud", "description": "Create / read / complete / fail tasks."},
            {"name": "bulletin", "description": "Post and read cross-agent bulletins."},
            {"name": "status", "description": "Read server status and health probes."},
        ],
        "skills": list(_SKILLS),
        "defaultInputModes": ["text", "application/json"],
        "defaultOutputModes": ["application/json"],
        "authentication": {
            "schemes": ["Bearer"],
            "publicPaths": ["/health", "/.well-known/agent.json", "/llms.txt"],
            "description": (
                "Bearer token in Authorization header.  Set BERNSTEIN_AUTH_DISABLED=1 "
                "for local development (no token required)."
            ),
        },
        "endpoints": [{"method": e.method, "path": e.path, "summary": e.summary} for e in _ENDPOINTS],
    }


def _render_llms_txt() -> str:
    """Render the markdown summary served at /llms.txt."""
    lines: list[str] = [
        f"# {_AGENT_NAME}",
        "",
        f"> {_AGENT_DESCRIPTION}",
        "",
        f"- Version: {_BERNSTEIN_VERSION}",
        f"- Protocol: A2A {_PROTOCOL_VERSION}",
        f"- Docs: {_DOCS_URL}",
        "",
        "## Endpoints",
        "",
    ]
    lines.extend(f"- `{e.method} {e.path}` — {e.summary}" for e in _ENDPOINTS)
    lines += [
        "",
        "## Auth",
        "",
        "Send `Authorization: Bearer <token>` on every request.  Public paths: "
        "`/health`, `/.well-known/agent.json`, `/llms.txt`.",
        "",
    ]
    return "\n".join(lines)


@router.get("/.well-known/agent.json", include_in_schema=False)
def agent_json() -> dict[str, Any]:
    """Return the A2A-compliant agent card for this task server."""
    return _agent_card_payload()


@router.get("/llms.txt", include_in_schema=False, response_class=PlainTextResponse)
def llms_txt() -> str:
    """Return a markdown summary of the public API surface."""
    return _render_llms_txt()
