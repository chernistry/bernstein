"""Tests for static service manifest routes (/.well-known/agent.json, /llms.txt).

Covers both the legacy server-info expectations and the A2A v1.0 surface
landed in feat/a2a-v1-well-known-agent-json: signed-card body, JWKS keys
endpoint, JCS-canonical body, signature verification round-trip.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bernstein.core.agents.claude_agent_card import parse_agent_card
from bernstein.core.routes.well_known import (
    _DEFAULT_KID,
    _ENDPOINTS,
    _agent_card_payload,
    _render_llms_txt,
    _reset_signing_keypair_for_tests,
)
from bernstein.core.security.agent_card_signer import canonicalize_jcs
from bernstein.core.security.auth_middleware import AUTH_PUBLIC_PATHS
from bernstein.core.server import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    os.environ["BERNSTEIN_AUTH_DISABLED"] = "1"
    _reset_signing_keypair_for_tests()
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")
    return TestClient(app)


def test_agent_json_returns_valid_a2a_card(client: TestClient) -> None:
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    data = resp.json()
    card = parse_agent_card(data)
    assert card.name == "bernstein"
    assert card.protocol_version
    assert card.version
    assert card.url
    assert any(c.name == "task-crud" for c in card.capabilities)
    assert any(s.id == "task-orchestration" for s in card.skills)


def test_agent_json_lists_documented_endpoints(client: TestClient) -> None:
    resp = client.get("/.well-known/agent.json")
    endpoints = resp.json()["endpoints"]
    paths = {(e["method"], e["path"]) for e in endpoints}
    assert ("POST", "/tasks") in paths
    assert ("POST", "/tasks/{id}/complete") in paths
    assert ("POST", "/bulletin") in paths
    assert ("GET", "/bulletin") in paths


def test_llms_txt_is_markdown(client: TestClient) -> None:
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert body.startswith("# bernstein")
    assert "## Endpoints" in body
    assert "## Auth" in body


def test_llms_txt_mentions_every_documented_endpoint() -> None:
    """Regression guard: adding an endpoint to the manifest must surface in llms.txt.

    The single ``_ENDPOINTS`` table feeds both renderers, so this test will
    fail loudly if the markdown template ever stops iterating it.
    """
    body = _render_llms_txt()
    for endpoint in _ENDPOINTS:
        assert endpoint.path in body, f"missing {endpoint.path}"
        assert endpoint.method in body, f"missing {endpoint.method}"


def test_well_known_paths_are_public_in_auth_middleware() -> None:
    assert "/.well-known/agent.json" in AUTH_PUBLIC_PATHS
    assert "/.well-known/agent.json/keys" in AUTH_PUBLIC_PATHS
    assert "/llms.txt" in AUTH_PUBLIC_PATHS


def test_agent_card_payload_supports_custom_base_url() -> None:
    payload = _agent_card_payload(base_url="https://api.example.com")
    assert payload["url"] == "https://api.example.com"
    assert payload["authentication"]["schemes"] == ["Bearer"]


# ---------------------------------------------------------------------------
# A2A v1.0 surface — protocolVersion, supportedInterfaces, securitySchemes,
# signatures.
# ---------------------------------------------------------------------------


def test_agent_json_advertises_protocol_v1(client: TestClient) -> None:
    """A2A v1.0 conformance flag — verifiers route off this string."""
    data = client.get("/.well-known/agent.json").json()
    assert data["protocolVersion"] == "1.0"


def test_agent_json_supported_interfaces(client: TestClient) -> None:
    data = client.get("/.well-known/agent.json").json()
    assert "HTTP+JSON" in data["supportedInterfaces"]


def test_agent_json_security_schemes_include_bearer(client: TestClient) -> None:
    data = client.get("/.well-known/agent.json").json()
    schemes = {s["id"]: s for s in data["securitySchemes"]}
    assert "bearer-jwt" in schemes
    assert schemes["bearer-jwt"]["scheme"] == "Bearer"
    assert schemes["bearer-jwt"]["required"] is True
    # mTLS is declared as a forward-compat stub (deferred).
    assert "mtls" in schemes
    assert schemes["mtls"]["required"] is False


def test_agent_json_signature_present(client: TestClient) -> None:
    data = client.get("/.well-known/agent.json").json()
    assert isinstance(data["signatures"], list)
    assert len(data["signatures"]) == 1
    sig = data["signatures"][0]
    assert sig["alg"] == "EdDSA"
    assert sig["typ"] == "agent-card+jws"
    assert sig["kid"] == _DEFAULT_KID
    # Detached JWS — header..signature shape.
    parts = sig["jws"].split(".")
    assert len(parts) == 3
    assert parts[1] == "", "JWS payload segment must be empty (RFC 7515 A.5)"


def test_agent_json_body_is_jcs_canonical(client: TestClient) -> None:
    """Raw response bytes must match ``canonicalize_jcs`` of the parsed dict."""
    resp = client.get("/.well-known/agent.json")
    raw = resp.content
    parsed = json.loads(raw)
    recanonical = canonicalize_jcs(parsed)
    assert raw == recanonical, f"body is not JCS-canonical:\n  got: {raw!r}\n  want: {recanonical!r}"
    # Cache header per ticket spec.
    assert resp.headers.get("cache-control") == "public, max-age=3600"


# ---------------------------------------------------------------------------
# JWKS endpoint
# ---------------------------------------------------------------------------


def test_jwks_endpoint_shape(client: TestClient) -> None:
    resp = client.get("/.well-known/agent.json/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert isinstance(data["keys"], list)
    assert len(data["keys"]) >= 1
    jwk = data["keys"][0]
    assert jwk["kty"] == "OKP"
    assert jwk["crv"] == "Ed25519"
    assert jwk["alg"] == "EdDSA"
    assert jwk["use"] == "sig"
    assert jwk["kid"] == _DEFAULT_KID
    # x is the raw 32-byte public key, base64url without padding.
    raw = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
    assert len(raw) == 32


def test_jws_signature_verifies_against_jwks(client: TestClient) -> None:
    """End-to-end: sign on /agent.json verifies with the JWKS pubkey.

    This is the contract a third-party A2A v1.0 verifier executes — strip
    ``signatures`` from the body, JCS-canonicalise the rest, and verify
    each signature against the matching ``kid`` from the JWKS endpoint.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    card_resp = client.get("/.well-known/agent.json")
    keys_resp = client.get("/.well-known/agent.json/keys")
    card = json.loads(card_resp.content)
    sig = card["signatures"][0]
    jwks = keys_resp.json()
    jwk = next(k for k in jwks["keys"] if k["kid"] == sig["kid"])
    raw_pub = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
    pub = Ed25519PublicKey.from_public_bytes(raw_pub)

    # Reconstruct the signing input: header_b64 + "." + body_b64 where the
    # body is the JCS canonicalisation of the response with ``signatures``
    # stripped.
    header_b64, _empty, sig_b64 = sig["jws"].split(".")
    body_for_signing = {k: v for k, v in card.items() if k != "signatures"}
    canonical = canonicalize_jcs(body_for_signing)
    body_b64 = base64.urlsafe_b64encode(canonical).rstrip(b"=").decode("ascii")
    signing_input = f"{header_b64}.{body_b64}".encode("ascii")
    signature_bytes = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))

    # No exception → signature is valid.
    pub.verify(signature_bytes, signing_input)


def test_jwks_endpoint_is_stable_across_calls(client: TestClient) -> None:
    """The cached keypair means JWKS bytes don't shift across GETs."""
    a = client.get("/.well-known/agent.json/keys").json()
    b = client.get("/.well-known/agent.json/keys").json()
    assert a == b
