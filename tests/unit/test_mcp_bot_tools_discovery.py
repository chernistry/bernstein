"""Tests for the docs-bot MCP-tool discovery surface.

Covers:
    - Allowlist boundaries (only the four read-only tools, no write tools).
    - Off-by-default behaviour (env var unset -> empty payload, 200).
    - On behaviour (env var set -> all four tool specs returned).
    - HTTP route mounted under ``/.well-known/mcp-tools`` and accessible
      without auth.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bernstein.core.protocols.mcp_bot_allowlist import (
    ALLOWED_BOT_TOOLS,
    BOT_TOOL_SPECS,
    all_allowed_specs,
    filter_to_allowed,
)
from bernstein.core.routes.mcp_bot_tools import (
    DISCOVERY_PATH,
    _is_enabled,
    discovery_payload,
)
from bernstein.core.security.auth_middleware import AUTH_PUBLIC_PATHS
from bernstein.core.server import create_app

# ---------------------------------------------------------------------------
# Allowlist invariants
# ---------------------------------------------------------------------------


def test_allowlist_contains_only_read_only_tools() -> None:
    """The four read-only tools, nothing else (no run/approve/stop/subtask)."""
    assert (
        frozenset(
            {"bernstein_status", "bernstein_tasks", "bernstein_health", "bernstein_cost"},
        )
        == ALLOWED_BOT_TOOLS
    )


@pytest.mark.parametrize(
    "write_tool",
    ["bernstein_run", "bernstein_approve", "bernstein_stop", "bernstein_create_subtask"],
)
def test_write_tools_blocked_by_allowlist(write_tool: str) -> None:
    """Mutation tools must never appear in the allowlist."""
    assert write_tool not in ALLOWED_BOT_TOOLS
    assert filter_to_allowed([write_tool]) == []


def test_specs_match_allowlist() -> None:
    """Every published spec must be on the allowlist (no orphans)."""
    spec_names = {spec.name for spec in BOT_TOOL_SPECS}
    assert spec_names == ALLOWED_BOT_TOOLS


def test_filter_drops_unknown_names() -> None:
    """Unknown tool names are silently dropped - fail-open at the boundary."""
    out = filter_to_allowed(["bernstein_status", "totally_made_up_tool", "bernstein_run"])
    names = [s.name for s in out]
    assert names == ["bernstein_status"]


def test_all_allowed_specs_returns_full_set() -> None:
    specs = all_allowed_specs()
    assert {s.name for s in specs} == ALLOWED_BOT_TOOLS


# ---------------------------------------------------------------------------
# Discovery payload (pure function)
# ---------------------------------------------------------------------------


def test_discovery_payload_when_disabled_is_empty() -> None:
    payload = discovery_payload(enabled=False)
    assert payload == {"version": 1, "enabled": False, "tools": []}


def test_discovery_payload_when_enabled_lists_all_four_tools() -> None:
    payload = discovery_payload(enabled=True)
    assert payload["version"] == 1
    assert payload["enabled"] is True
    names = {t["name"] for t in payload["tools"]}
    assert names == ALLOWED_BOT_TOOLS
    # Every tool entry must have a non-empty summary.
    for tool in payload["tools"]:
        assert tool["summary"]


def test_discovery_payload_respects_filtered_specs() -> None:
    """Caller-supplied specs are used verbatim when ``enabled=True``."""
    only_status = filter_to_allowed(["bernstein_status"])
    payload = discovery_payload(enabled=True, specs=only_status)
    assert [t["name"] for t in payload["tools"]] == ["bernstein_status"]


# ---------------------------------------------------------------------------
# _is_enabled env handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "ON"])
def test_is_enabled_truthy_values(value: str) -> None:
    assert _is_enabled({"BERNSTEIN_BOT_TOOLS_ENABLED": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_is_enabled_falsy_values(value: str) -> None:
    assert _is_enabled({"BERNSTEIN_BOT_TOOLS_ENABLED": value}) is False


def test_is_enabled_unset_defaults_to_false() -> None:
    assert _is_enabled({}) is False


# ---------------------------------------------------------------------------
# HTTP integration
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("BERNSTEIN_AUTH_DISABLED", "1")
    # Default: flag off so the off-by-default test sees the disabled payload.
    monkeypatch.delenv("BERNSTEIN_BOT_TOOLS_ENABLED", raising=False)
    app = create_app(jsonl_path=tmp_path / "tasks.jsonl")
    return TestClient(app)


def test_route_returns_disabled_payload_by_default(client: TestClient) -> None:
    resp = client.get(DISCOVERY_PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["tools"] == []
    assert body["version"] == 1


def test_route_returns_full_tool_list_when_enabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BERNSTEIN_BOT_TOOLS_ENABLED", "1")
    resp = client.get(DISCOVERY_PATH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    names = {t["name"] for t in body["tools"]}
    assert names == ALLOWED_BOT_TOOLS


def test_route_is_in_public_auth_paths() -> None:
    """Discovery is anonymous - must live in AUTH_PUBLIC_PATHS."""
    assert DISCOVERY_PATH in AUTH_PUBLIC_PATHS


def test_route_does_not_503_when_disabled(client: TestClient) -> None:
    """Aporia fail-open contract: disabled state still 200, never an error."""
    resp = client.get(DISCOVERY_PATH)
    assert resp.status_code == 200


def test_route_unaffected_by_unrelated_env(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting other env vars must not flip the bot-tools flag accidentally."""
    monkeypatch.setenv("SOME_OTHER_FLAG", "1")
    resp = client.get(DISCOVERY_PATH)
    assert resp.json()["enabled"] is False


def teardown_module() -> None:
    """Defensive: scrub the env var so other tests don't see leaked state."""
    os.environ.pop("BERNSTEIN_BOT_TOOLS_ENABLED", None)
