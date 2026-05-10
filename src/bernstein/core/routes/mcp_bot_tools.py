"""Discovery endpoint for the bernstein.run docs-bot tool surface.

Exposes ``GET /.well-known/mcp-tools`` so the bernstein_landing docs bot
can probe whether this Bernstein cluster is willing to be
called by the bot, and which read-only tools are on offer.

Off by default. Enabled by setting the env var
``BERNSTEIN_BOT_TOOLS_ENABLED=1`` (or the corresponding ``.sdd/config.yaml``
``bot_tools.enabled: true`` flag, surfaced by the harness).

Contract — the response body is intentionally minimal::

    {
      "version": 1,
      "enabled": true,
      "tools": [
        {"name": "bernstein_status", "summary": "..."},
        ...
      ]
    }

When the flag is off the body is::

    {"version": 1, "enabled": false, "tools": []}

Either way the endpoint returns 200, so a docs-bot probing it can
distinguish "Bernstein reachable but bot tools off" from "Bernstein not
reachable at all" (which the bot already handles via timeout in its
fail-open discovery loop). This is the Aporia fail-open pattern made
explicit on the *server* side: the endpoint never 4xx/5xx for a configured
state choice.

References:
    - .sdd/backlog/open/2026-05-08-bernstein-mcp-bot-tools-exposure.md
    - bernstein_landing/.sdd/backlog/open/2026-05-08-rag-001-shared-ai-gateway-service.md
"""

from __future__ import annotations

import os
from typing import Any, Final

from fastapi import APIRouter

from bernstein.core.protocols.mcp_bot_allowlist import (
    BotToolSpec,
    all_allowed_specs,
)

router = APIRouter()

# Public discovery path; mirrored into AUTH_PUBLIC_PATHS so anonymous
# callers (the bernstein.run gateway probing whether to inject tools) can
# read it without provisioning a token.
DISCOVERY_PATH: Final[str] = "/.well-known/mcp-tools"

# Env var driving the off-by-default flag.  Truthy values: ``1``, ``true``,
# ``yes``, ``on`` (case-insensitive).  Anything else (including unset) keeps
# the endpoint disabled.
_ENABLE_ENV_VAR: Final[str] = "BERNSTEIN_BOT_TOOLS_ENABLED"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

# Schema-version sentinel — bumped if the discovery payload shape changes.
# Kept out of the response body's tool entries so the docs bot can switch
# parsers based on this single integer.
_SCHEMA_VERSION: Final[int] = 1


def _is_enabled(env: dict[str, str] | None = None) -> bool:
    """Return ``True`` when the bot-tools surface is enabled.

    Args:
        env: Optional environment override (defaults to ``os.environ``).
            Tests inject a stub mapping; production reads the live env on
            every call so an operator can flip the flag with a sighup-free
            container restart.

    Returns:
        ``True`` iff the env var resolves to a truthy literal.
    """
    source = env if env is not None else os.environ
    raw = source.get(_ENABLE_ENV_VAR, "").strip().lower()
    return raw in _TRUTHY


def _serialise_specs(specs: list[BotToolSpec]) -> list[dict[str, Any]]:
    """Convert ``BotToolSpec``s to the JSON-shaped dicts the bot consumes."""
    return [spec.to_dict() for spec in specs]


def discovery_payload(*, enabled: bool, specs: list[BotToolSpec] | None = None) -> dict[str, Any]:
    """Build the response body for the discovery endpoint.

    Pulled out of the route handler so tests can assert payload shape
    without spinning up a TestClient, and so a future client-side helper
    can render the same body deterministically.

    Args:
        enabled: Whether the surface is active.
        specs: Allowlisted tool specs (defaults to :func:`all_allowed_specs`
            when ``enabled`` is true; ignored otherwise).

    Returns:
        JSON-serialisable dict with keys ``version``, ``enabled``, ``tools``.
    """
    if not enabled:
        return {"version": _SCHEMA_VERSION, "enabled": False, "tools": []}
    resolved = specs if specs is not None else all_allowed_specs()
    return {
        "version": _SCHEMA_VERSION,
        "enabled": True,
        "tools": _serialise_specs(resolved),
    }


@router.get(DISCOVERY_PATH, include_in_schema=False)
def mcp_tools_discovery() -> dict[str, Any]:
    """Return the bot-callable MCP tool list (or an empty list when off).

    Always 200. The docs bot's fail-open discovery treats both
    ``enabled=false`` and a network error identically — it skips tool
    injection and answers from passages alone — so we never need to 503
    just because an operator has the flag off.
    """
    return discovery_payload(enabled=_is_enabled())


__all__ = [
    "DISCOVERY_PATH",
    "discovery_payload",
    "router",
]
