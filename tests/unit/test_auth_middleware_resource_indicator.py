"""Tests for RFC 8707 resource indicator validation in ``SSOAuthMiddleware``.

Covers:
- A token with a matching ``resource`` claim passes through.
- A token with a mismatched ``resource`` claim is rejected with HTTP 401
  and the RFC 6750 ``WWW-Authenticate: Bearer error="invalid_token"`` header.
- Unset configuration skips the check entirely so existing deployments
  don't break.
- A list of expected resources allows any-match.
- A malformed ``resource`` claim is rejected with the malformed challenge.
- Tokens that omit the claim entirely pass through (legacy bearer flows).
"""

# pyright: reportPrivateUsage=false, reportUntypedFunctionDecorator=false, reportUnknownMemberType=false

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from bernstein.core.security.auth import (
    AuthService,
    AuthStore,
    AuthUser,
    SSOConfig,
    create_jwt,
)
from bernstein.core.security.auth_middleware import (
    AUTH_EXPECTED_RESOURCE_ENV,
    SSOAuthMiddleware,
    _normalise_expected_resource,
    expected_resource_from_env,
)

pytestmark = pytest.mark.auth_enabled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sso(tmp_path: Path) -> tuple[AuthService, str, str]:
    """Build a minimal SSO auth service and a valid JWT for it.

    Returns:
        ``(auth_service, jwt_secret, valid_token)``. The token has
        ``sub=test-user``; tests further customise extra claims (notably
        ``resource``) via ``create_jwt`` calls of their own.
    """
    config = SSOConfig(jwt_secret="resource-test-secret", enabled=True)  # NOSONAR - test fixture
    store = AuthStore(tmp_path)
    user = AuthUser(id="test-user", email="ru@example.com", display_name="Resource Test")
    store.save_user(user)
    service = AuthService(config, store)
    token = create_jwt(
        {"sub": user.id, "session_id": ""},
        config.jwt_secret,
        expiry_seconds=600,
    )
    return service, config.jwt_secret, token


def _build_app(
    *,
    auth_service: AuthService | None = None,
    legacy_token: str | None = None,
    expected_resource: Any = None,
) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        SSOAuthMiddleware,
        auth_service=auth_service,
        legacy_token=legacy_token,
        expected_resource=expected_resource,
    )

    @app.get("/status")
    async def status_route(request: Request) -> JSONResponse:
        del request
        return JSONResponse({"ok": True})

    return TestClient(app)


# ---------------------------------------------------------------------------
# Configuration parsing
# ---------------------------------------------------------------------------


class TestExpectedResourceParsing:
    def test_none_means_disabled(self) -> None:
        assert _normalise_expected_resource(None) == ()

    def test_empty_string_means_disabled(self) -> None:
        assert _normalise_expected_resource("") == ()
        assert _normalise_expected_resource("   ") == ()

    def test_single_string(self) -> None:
        assert _normalise_expected_resource("https://api.example") == ("https://api.example",)

    def test_comma_separated_string(self) -> None:
        assert _normalise_expected_resource("https://a,https://b") == (
            "https://a",
            "https://b",
        )

    def test_list_of_strings(self) -> None:
        assert _normalise_expected_resource(["https://a", "https://b"]) == (
            "https://a",
            "https://b",
        )

    def test_env_var_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(AUTH_EXPECTED_RESOURCE_ENV, "https://api.bernstein.dev")
        assert expected_resource_from_env() == ("https://api.bernstein.dev",)


# ---------------------------------------------------------------------------
# Default-off - empty config skips the check
# ---------------------------------------------------------------------------


class TestUnsetSkipsCheck:
    def test_unset_skips_check_for_legacy_token(self) -> None:
        """A legacy bearer token without a resource claim must pass."""
        client = _build_app(legacy_token="secret")
        resp = client.get("/status", headers={"Authorization": "Bearer secret"})
        assert resp.status_code == 200

    def test_unset_skips_check_for_sso_token(self, tmp_path: Path) -> None:
        service, _, token = _build_sso(tmp_path)
        client = _build_app(auth_service=service)
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Match / mismatch
# ---------------------------------------------------------------------------


class TestResourceMatchAndMismatch:
    def test_matching_single_resource_passes(self, tmp_path: Path) -> None:
        service, secret, _default_token = _build_sso(tmp_path)
        token = create_jwt(
            {
                "sub": "test-user",
                "session_id": "",
                "resource": "https://api.bernstein.dev",
            },
            secret,
            expiry_seconds=600,
        )
        client = _build_app(
            auth_service=service,
            expected_resource="https://api.bernstein.dev",
        )
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_matching_one_of_list_passes(self, tmp_path: Path) -> None:
        service, secret, _ = _build_sso(tmp_path)
        token = create_jwt(
            {"sub": "test-user", "session_id": "", "resource": "https://b.example"},
            secret,
            expiry_seconds=600,
        )
        client = _build_app(
            auth_service=service,
            expected_resource=["https://a.example", "https://b.example"],
        )
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_mismatch_returns_401_with_www_authenticate(self, tmp_path: Path) -> None:
        service, secret, _ = _build_sso(tmp_path)
        token = create_jwt(
            {
                "sub": "test-user",
                "session_id": "",
                "resource": "https://attacker.example",
            },
            secret,
            expiry_seconds=600,
        )
        client = _build_app(
            auth_service=service,
            expected_resource="https://api.bernstein.dev",
        )
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        # RFC 6750 challenge with RFC 8707 wording.
        challenge = resp.headers.get("www-authenticate", "")
        assert "Bearer" in challenge
        assert 'error="invalid_token"' in challenge
        assert "resource indicator mismatch" in challenge

    def test_token_without_resource_claim_passes(self, tmp_path: Path) -> None:
        """RFC 8707 enforcement only kicks in when the claim is present.

        Legacy tokens minted before the orchestrator started enforcing the
        indicator must keep validating; otherwise upgrade breaks every
        running fleet.
        """
        service, secret, _ = _build_sso(tmp_path)
        token = create_jwt(
            {"sub": "test-user", "session_id": ""},
            secret,
            expiry_seconds=600,
        )
        client = _build_app(
            auth_service=service,
            expected_resource="https://api.bernstein.dev",
        )
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_malformed_claim_rejected(self, tmp_path: Path) -> None:
        """Non-string/non-list resource value is malformed per RFC 8707 §2."""
        service, secret, _ = _build_sso(tmp_path)
        token = create_jwt(
            {"sub": "test-user", "session_id": "", "resource": 12345},
            secret,
            expiry_seconds=600,
        )
        client = _build_app(
            auth_service=service,
            expected_resource="https://api.bernstein.dev",
        )
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "malformed resource indicator" in resp.headers.get("www-authenticate", "")

    def test_resource_claim_array_any_match_passes(self, tmp_path: Path) -> None:
        """RFC 8707 §2 lets the token carry an array of resources - any-match."""
        service, secret, _ = _build_sso(tmp_path)
        token = create_jwt(
            {
                "sub": "test-user",
                "session_id": "",
                "resource": ["https://other.example", "https://api.bernstein.dev"],
            },
            secret,
            expiry_seconds=600,
        )
        client = _build_app(
            auth_service=service,
            expected_resource="https://api.bernstein.dev",
        )
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_env_var_supplies_default_when_arg_omitted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``BERNSTEIN_AUTH_EXPECTED_RESOURCE`` is honoured when no arg is passed."""
        monkeypatch.setenv(AUTH_EXPECTED_RESOURCE_ENV, "https://api.bernstein.dev")
        service, secret, _ = _build_sso(tmp_path)
        token = create_jwt(
            {"sub": "test-user", "session_id": "", "resource": "https://attacker.example"},
            secret,
            expiry_seconds=600,
        )
        # No expected_resource arg - env var supplies it.
        client = _build_app(auth_service=service)
        resp = client.get("/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
