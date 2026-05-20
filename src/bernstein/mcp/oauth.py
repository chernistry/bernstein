"""OAuth-2 / OIDC discovery metadata for the Bernstein MCP server.

Hosts that auto-discover MCP servers look for the standard OAuth-2
authorization-server metadata document (RFC 8414) and the MCP-style
protected-resource metadata document (draft) to learn:

  * which authorization server (IdP) issues tokens for this resource;
  * which scopes the resource accepts;
  * which response/grant types and PKCE methods are advertised.

Bernstein itself does not issue tokens. Instead, when the operator points the
server at an external IdP (via ``BERNSTEIN_MCP_OAUTH_ISSUER``), this module
publishes the discovery metadata so a client can negotiate a PKCE flow with
that IdP and then present the resulting bearer token to the streamable HTTP
transport. The transport's existing static-bearer check validates the token
opaquely; full JWKS validation against the issuer is a follow-up.

This closes the discovery gap for the OAuth-2 PKCE auth path: a client that
auto-discovers protected-resource metadata can locate the IdP without trial
and error. When the issuer env var is not set, the discovery endpoints
return 404 so anonymous/static-bearer flows remain the only advertised path.
"""

from __future__ import annotations

import os
from typing import Any

#: Env var that configures the OAuth-2 issuer URL Bernstein advertises.
#: When unset, discovery endpoints return 404.
ISSUER_ENV: str = "BERNSTEIN_MCP_OAUTH_ISSUER"

#: Env var holding the comma-separated scopes the resource server accepts.
SCOPES_ENV: str = "BERNSTEIN_MCP_OAUTH_SCOPES"

#: Default scopes advertised when ``BERNSTEIN_MCP_OAUTH_SCOPES`` is unset.
_DEFAULT_SCOPES: tuple[str, ...] = ("bernstein.read", "bernstein.write")

#: Path where authorization-server metadata is served (RFC 8414).
AS_METADATA_PATH: str = "/.well-known/oauth-authorization-server"

#: Path where protected-resource metadata is served (MCP draft / RFC 9728).
PR_METADATA_PATH: str = "/.well-known/oauth-protected-resource"


def issuer() -> str:
    """Return the configured OAuth-2 issuer URL, or an empty string."""
    return os.environ.get(ISSUER_ENV, "").strip().rstrip("/")


def scopes_supported() -> list[str]:
    """Return the scopes advertised by the protected-resource metadata."""
    raw = os.environ.get(SCOPES_ENV, "").strip()
    if not raw:
        return list(_DEFAULT_SCOPES)
    return [s.strip() for s in raw.split(",") if s.strip()]


def oauth_discovery_enabled() -> bool:
    """Return True when the OAuth issuer is configured."""
    return bool(issuer())


def authorization_server_metadata() -> dict[str, Any] | None:
    """Build the RFC 8414 authorization-server metadata document.

    Returns:
        A JSON-serialisable dict, or ``None`` when no issuer is configured.
    """
    iss = issuer()
    if not iss:
        return None
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/oauth/authorize",
        "token_endpoint": f"{iss}/oauth/token",
        "jwks_uri": f"{iss}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_basic",
            "none",  # public client + PKCE
        ],
        "scopes_supported": scopes_supported(),
    }


def protected_resource_metadata(resource_url: str) -> dict[str, Any] | None:
    """Build the protected-resource metadata document (RFC 9728 / MCP draft).

    Args:
        resource_url: Absolute URL of the MCP resource (the streamable HTTP
            transport endpoint), used as the ``resource`` field.

    Returns:
        A JSON-serialisable dict, or ``None`` when no issuer is configured.
    """
    iss = issuer()
    if not iss:
        return None
    return {
        "resource": resource_url,
        "authorization_servers": [iss],
        "scopes_supported": scopes_supported(),
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://github.com/sipyourdrink-ltd/bernstein/blob/main/docs/mcp/server.md",
    }


def capability_card_oauth() -> dict[str, Any]:
    """Return the ``auth.oauth`` subtree for the runtime capability card.

    Reports whether the discovery surface is live and which issuer it points
    at, so a client that fetches the card can locate the metadata documents
    without probing the well-known paths.
    """
    iss = issuer()
    return {
        "enabled": bool(iss),
        "issuer": iss,
        "authorizationServerMetadata": AS_METADATA_PATH,
        "protectedResourceMetadata": PR_METADATA_PATH,
        "envVar": ISSUER_ENV,
    }
