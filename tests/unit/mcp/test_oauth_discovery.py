"""Tests for OAuth-2 / OIDC discovery metadata (issue #1674).

Covers:

  * the discovery helpers return ``None`` when no issuer is configured;
  * with an issuer set, the authorization-server metadata is RFC 8414 shaped;
  * the protected-resource metadata carries the configured authorization
    server and the resource URL;
  * the streamable HTTP transport serves both well-known paths and falls
    back to 404 when discovery is off;
  * the capability card reports the discovery state.
"""

from __future__ import annotations

import asyncio
import json

import pytest

# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERNSTEIN_MCP_OAUTH_ISSUER", raising=False)
    monkeypatch.delenv("BERNSTEIN_MCP_OAUTH_SCOPES", raising=False)


@pytest.fixture
def _with_issuer(monkeypatch: pytest.MonkeyPatch) -> str:
    issuer = "https://idp.example.com"
    monkeypatch.setenv("BERNSTEIN_MCP_OAUTH_ISSUER", issuer)
    monkeypatch.delenv("BERNSTEIN_MCP_OAUTH_SCOPES", raising=False)
    return issuer


def test_no_issuer_returns_none(_no_issuer: None) -> None:
    from bernstein.mcp.oauth import (
        authorization_server_metadata,
        oauth_discovery_enabled,
        protected_resource_metadata,
    )

    assert oauth_discovery_enabled() is False
    assert authorization_server_metadata() is None
    assert protected_resource_metadata("https://example.com/mcp") is None


def test_authorization_server_metadata_is_rfc8414_shaped(_with_issuer: str) -> None:
    from bernstein.mcp.oauth import authorization_server_metadata

    meta = authorization_server_metadata()
    assert meta is not None
    assert meta["issuer"] == _with_issuer
    assert meta["authorization_endpoint"].startswith(_with_issuer)
    assert meta["token_endpoint"].startswith(_with_issuer)
    assert "code" in meta["response_types_supported"]
    assert "authorization_code" in meta["grant_types_supported"]
    assert "S256" in meta["code_challenge_methods_supported"]
    # Public client + PKCE path is advertised.
    assert "none" in meta["token_endpoint_auth_methods_supported"]


def test_authorization_server_metadata_strips_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bernstein.mcp.oauth import authorization_server_metadata

    monkeypatch.setenv("BERNSTEIN_MCP_OAUTH_ISSUER", "https://idp.example.com/")
    meta = authorization_server_metadata()
    assert meta is not None
    # Trailing slash is normalised so the endpoints are well-formed.
    assert meta["issuer"] == "https://idp.example.com"
    assert meta["authorization_endpoint"] == "https://idp.example.com/oauth/authorize"


def test_protected_resource_metadata_carries_resource(_with_issuer: str) -> None:
    from bernstein.mcp.oauth import protected_resource_metadata

    meta = protected_resource_metadata("https://bernstein.example.com/mcp")
    assert meta is not None
    assert meta["resource"] == "https://bernstein.example.com/mcp"
    assert meta["authorization_servers"] == [_with_issuer]
    assert "header" in meta["bearer_methods_supported"]


def test_custom_scopes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.mcp.oauth import scopes_supported

    monkeypatch.setenv("BERNSTEIN_MCP_OAUTH_SCOPES", "foo, bar, baz")
    assert scopes_supported() == ["foo", "bar", "baz"]


# ---------------------------------------------------------------------------
# Streamable HTTP transport well-known paths
# ---------------------------------------------------------------------------


def test_transport_serves_authorization_server_metadata(_with_issuer: str) -> None:
    from bernstein.mcp.remote_transport import (
        RemoteMCPConfig,
        StreamableHTTPTransport,
    )

    cfg = RemoteMCPConfig(host="127.0.0.1", auth_type="none")
    transport = StreamableHTTPTransport(config=cfg)
    status, headers, body = asyncio.run(
        transport.handle_request(
            "GET",
            "/.well-known/oauth-authorization-server",
            {"host": "bernstein.example.com"},
            b"",
        )
    )
    assert status == 200
    assert headers["content-type"] == "application/json"
    payload = json.loads(body)
    assert payload["issuer"] == _with_issuer


def test_transport_serves_protected_resource_metadata(_with_issuer: str) -> None:
    from bernstein.mcp.remote_transport import (
        RemoteMCPConfig,
        StreamableHTTPTransport,
    )

    cfg = RemoteMCPConfig(host="127.0.0.1", auth_type="none")
    transport = StreamableHTTPTransport(config=cfg)
    status, _, body = asyncio.run(
        transport.handle_request(
            "GET",
            "/.well-known/oauth-protected-resource",
            {"host": "bernstein.example.com", "x-forwarded-proto": "https"},
            b"",
        )
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["resource"] == "https://bernstein.example.com/mcp"
    assert payload["authorization_servers"] == [_with_issuer]


def test_transport_returns_404_when_discovery_disabled(_no_issuer: None) -> None:
    from bernstein.mcp.remote_transport import (
        RemoteMCPConfig,
        StreamableHTTPTransport,
    )

    cfg = RemoteMCPConfig(host="127.0.0.1", auth_type="none")
    transport = StreamableHTTPTransport(config=cfg)
    status, _, _ = asyncio.run(
        transport.handle_request(
            "GET",
            "/.well-known/oauth-authorization-server",
            {"host": "127.0.0.1"},
            b"",
        )
    )
    assert status == 404


def test_well_known_path_does_not_require_auth(_with_issuer: str) -> None:
    """A client probing discovery has no token yet; the path must not 401."""
    from bernstein.mcp.remote_transport import (
        RemoteMCPConfig,
        StreamableHTTPTransport,
    )

    # Bearer config that would 401 any /mcp request without Authorization.
    cfg = RemoteMCPConfig(
        host="127.0.0.1",
        auth_type="bearer",
        auth_token="secret",
    )
    transport = StreamableHTTPTransport(config=cfg)
    status, _, _ = asyncio.run(
        transport.handle_request(
            "GET",
            "/.well-known/oauth-authorization-server",
            {"host": "127.0.0.1"},
            b"",
        )
    )
    assert status == 200


# ---------------------------------------------------------------------------
# Capability card integration
# ---------------------------------------------------------------------------


def test_capability_card_reports_oauth_state(_with_issuer: str) -> None:
    from bernstein.mcp.capability import build_capability_card

    card = build_capability_card()
    oauth = card["auth"]["oauth"]
    assert oauth["enabled"] is True
    assert oauth["issuer"] == _with_issuer
    assert oauth["authorizationServerMetadata"] == "/.well-known/oauth-authorization-server"
    # With discovery on, oauth2_pkce moves into supported.
    assert "oauth2_pkce" in card["auth"]["supported"]


def test_capability_card_reports_oauth_disabled(_no_issuer: None) -> None:
    from bernstein.mcp.capability import build_capability_card

    card = build_capability_card()
    assert card["auth"]["oauth"]["enabled"] is False
    # When off, oauth2_pkce remains planned, not supported.
    assert "oauth2_pkce" in card["auth"]["planned"]
    assert "oauth2_pkce" not in card["auth"]["supported"]
