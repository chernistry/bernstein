"""Tests for the substrate host registry and registration writes."""

from __future__ import annotations

import json

import pytest

from bernstein.core.substrate.host_registry import (
    HOST_REGISTRY,
    SERVER_ID,
    HostStatus,
    bernstein_server_entry,
    get_host,
    known_host_names,
)
from bernstein.core.substrate.register import (
    RegisterResult,
    is_registered,
    register_host,
)


def test_two_hosts_supported_rest_stubbed() -> None:
    """Exactly the two MVP hosts are supported; everything else is stubbed."""
    supported = {n for n, h in HOST_REGISTRY.items() if h.status is HostStatus.SUPPORTED}
    assert supported == {"claude-desktop", "claude-code"}
    stubbed = {n for n, h in HOST_REGISTRY.items() if h.status is HostStatus.STUBBED}
    assert {"cursor", "continue", "cline", "zed", "aider", "codex", "gemini"} <= stubbed


def test_known_host_names_sorted() -> None:
    """``known_host_names`` returns a stable alphabetised list."""
    names = known_host_names()
    assert names == sorted(names)
    assert "claude-desktop" in names


def test_get_host_unknown_raises_with_valid_list() -> None:
    """Unknown host lookups raise KeyError naming the valid hosts."""
    with pytest.raises(KeyError) as exc:
        get_host("not-a-host")
    assert "claude-desktop" in str(exc.value)


def test_bernstein_server_entry_shape() -> None:
    """The server entry launches the bundled MCP module."""
    entry = bernstein_server_entry()
    assert entry["args"] == ["-m", "bernstein.mcp"]
    assert isinstance(entry["command"], str)


def test_stubbed_host_register_rejected() -> None:
    """Registering a stubbed host raises rather than silently no-op."""
    cursor = get_host("cursor")
    assert not cursor.supported
    with pytest.raises(ValueError, match="not yet supported"):
        register_host(cursor)


# ---------------------------------------------------------------------------
# Registration writes (filesystem mocked via tmp_path)
# ---------------------------------------------------------------------------


def test_register_claude_desktop_creates_entry(tmp_path) -> None:
    """Registering Claude Desktop writes a bernstein mcpServers entry."""
    cfg = tmp_path / "claude_desktop_config.json"
    host = get_host("claude-desktop")

    result = register_host(host, path=cfg)

    assert isinstance(result, RegisterResult)
    assert result.action == "registered"
    assert result.backup_path is None  # no prior file -> no backup
    data = json.loads(cfg.read_text())
    assert data["mcpServers"][SERVER_ID] == bernstein_server_entry()


def test_register_idempotent_no_rewrite(tmp_path) -> None:
    """Re-registering an identical entry reports already_registered, no backup."""
    cfg = tmp_path / "claude_desktop_config.json"
    host = get_host("claude-desktop")
    register_host(host, path=cfg)

    again = register_host(host, path=cfg)
    assert again.action == "already_registered"
    assert again.backup_path is None
    # No stray backup files were created on the idempotent path.
    assert list(tmp_path.glob("*.bak")) == []


def test_register_backs_up_existing_config(tmp_path) -> None:
    """An existing config is backed up before a mutating write."""
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    host = get_host("claude-desktop")

    result = register_host(host, path=cfg)

    assert result.backup_path is not None
    assert result.backup_path.exists()
    # Backup preserves the pre-write content.
    backed = json.loads(result.backup_path.read_text())
    assert backed["theme"] == "dark"
    assert "bernstein" not in backed["mcpServers"]


def test_register_never_clobbers_unrelated_keys(tmp_path) -> None:
    """Merge preserves unrelated servers and top-level keys."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "globalShortcut": "Cmd+K"}))
    host = get_host("claude-desktop")

    register_host(host, path=cfg)

    data = json.loads(cfg.read_text())
    assert data["globalShortcut"] == "Cmd+K"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert SERVER_ID in data["mcpServers"]


def test_register_invalid_json_refuses(tmp_path) -> None:
    """A non-JSON existing config is not overwritten silently."""
    cfg = tmp_path / "config.json"
    cfg.write_text("{not valid json")
    host = get_host("claude-desktop")
    with pytest.raises(ValueError, match="not valid JSON"):
        register_host(host, path=cfg)


def test_is_registered_reflects_state(tmp_path) -> None:
    """``is_registered`` is False before and True after a write."""
    cfg = tmp_path / "config.json"
    host = get_host("claude-desktop")
    assert is_registered(host, path=cfg) is False
    register_host(host, path=cfg)
    assert is_registered(host, path=cfg) is True
