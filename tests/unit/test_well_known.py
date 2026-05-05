"""Tests for static service manifest routes (/.well-known/agent.json, /llms.txt)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bernstein.core.agents.claude_agent_card import parse_agent_card
from bernstein.core.routes.well_known import (
    _ENDPOINTS,
    _agent_card_payload,
    _render_llms_txt,
)
from bernstein.core.security.auth_middleware import AUTH_PUBLIC_PATHS
from bernstein.core.server import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    os.environ["BERNSTEIN_AUTH_DISABLED"] = "1"
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
    assert "/llms.txt" in AUTH_PUBLIC_PATHS


def test_agent_card_payload_supports_custom_base_url() -> None:
    payload = _agent_card_payload(base_url="https://api.example.com")
    assert payload["url"] == "https://api.example.com"
    assert payload["authentication"]["schemes"] == ["Bearer"]
