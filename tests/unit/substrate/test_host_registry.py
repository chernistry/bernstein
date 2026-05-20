"""Tests for the substrate host registry and registration writes."""

from __future__ import annotations

import json

import pytest

from bernstein.core.substrate.host_registry import (
    HOST_REGISTRY,
    SERVER_ID,
    ConfigFormat,
    HostStatus,
    bernstein_server_entry,
    get_host,
    known_host_names,
)
from bernstein.core.substrate.register import (
    RegisterResult,
    is_registered,
    is_stale,
    register_host,
)


def test_priority_hosts_supported() -> None:
    """All priority hosts (Claude pair + Cursor/Continue/Cline/Zed/Aider) are supported."""
    supported = {n for n, h in HOST_REGISTRY.items() if h.status is HostStatus.SUPPORTED}
    assert {"claude-desktop", "claude-code", "cursor", "continue", "cline", "zed", "aider"} <= supported


def test_remaining_hosts_still_stubbed() -> None:
    """Hosts not yet in the priority list remain stubbed."""
    stubbed = {n for n, h in HOST_REGISTRY.items() if h.status is HostStatus.STUBBED}
    # codex (TOML) and gemini stay stubbed pending their own packaging passes.
    assert {"codex", "gemini"} <= stubbed


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
    codex = get_host("codex")
    assert not codex.supported
    with pytest.raises(ValueError, match="not yet supported"):
        register_host(codex)


def test_zed_uses_context_servers_key() -> None:
    """Zed nests servers under ``context_servers``, not ``mcpServers``."""
    zed = get_host("zed")
    assert zed.config_key == "context_servers"
    assert zed.config_format is ConfigFormat.JSON


def test_aider_uses_yaml_format() -> None:
    """Aider's config is YAML; the registry must reflect that."""
    aider = get_host("aider")
    assert aider.config_format is ConfigFormat.YAML
    assert aider.config_key == "mcp-servers"


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


# ---------------------------------------------------------------------------
# New hosts: per-host smoke tests for the same write contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host_name", "config_key"),
    [
        ("cursor", "mcpServers"),
        ("continue", "mcpServers"),
        ("cline", "mcpServers"),
        ("zed", "context_servers"),
    ],
)
def test_register_json_host_writes_expected_key(tmp_path, host_name, config_key) -> None:
    """Cursor / Continue / Cline / Zed all write through the JSON path."""
    cfg = tmp_path / f"{host_name}.json"
    host = get_host(host_name)
    assert host.config_format is ConfigFormat.JSON
    assert host.config_key == config_key

    result = register_host(host, path=cfg)

    assert result.action == "registered"
    data = json.loads(cfg.read_text())
    assert data[config_key][SERVER_ID] == bernstein_server_entry()


def test_register_zed_preserves_unrelated_top_level_keys(tmp_path) -> None:
    """Zed config registration leaves editor settings (theme, fonts) intact."""
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"theme": "One Dark", "ui_font_size": 16}))
    host = get_host("zed")

    register_host(host, path=cfg)

    data = json.loads(cfg.read_text())
    assert data["theme"] == "One Dark"
    assert data["ui_font_size"] == 16
    assert SERVER_ID in data["context_servers"]


def test_register_aider_writes_yaml_entry(tmp_path) -> None:
    """Aider registration round-trips through YAML and records the entry."""
    cfg = tmp_path / "aider.yml"
    host = get_host("aider")

    result = register_host(host, path=cfg)

    assert result.action == "registered"
    import yaml

    data = yaml.safe_load(cfg.read_text())
    assert data["mcp-servers"][SERVER_ID] == bernstein_server_entry()


def test_register_aider_preserves_existing_yaml_keys(tmp_path) -> None:
    """Aider registration leaves unrelated YAML keys untouched."""
    cfg = tmp_path / "aider.yml"
    cfg.write_text("model: gpt-4o\nauto-commits: false\n")
    host = get_host("aider")

    result = register_host(host, path=cfg)

    assert result.backup_path is not None
    import yaml

    data = yaml.safe_load(cfg.read_text())
    assert data["model"] == "gpt-4o"
    assert data["auto-commits"] is False
    assert SERVER_ID in data["mcp-servers"]


def test_register_aider_idempotent(tmp_path) -> None:
    """A second Aider registration with identical content is a no-op."""
    cfg = tmp_path / "aider.yml"
    host = get_host("aider")
    register_host(host, path=cfg)

    again = register_host(host, path=cfg)
    assert again.action == "already_registered"
    assert again.backup_path is None


def test_register_aider_invalid_yaml_refuses(tmp_path) -> None:
    """Aider config with a broken YAML body is left untouched."""
    cfg = tmp_path / "aider.yml"
    cfg.write_text("model: gpt-4o\n  badly: indented: mapping: here\n: : :\n")
    host = get_host("aider")
    with pytest.raises(ValueError, match="not valid YAML|not a YAML mapping"):
        register_host(host, path=cfg)


def test_is_stale_detects_drift(tmp_path) -> None:
    """``is_stale`` returns True when the recorded command differs."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"mcpServers": {SERVER_ID: {"command": "/old/python", "args": ["-m", "bernstein.mcp"]}}}))
    host = get_host("claude-desktop")
    assert is_stale(host, path=cfg) is True


def test_is_stale_false_when_absent_or_current(tmp_path) -> None:
    """``is_stale`` is False for missing entries and for current entries."""
    cfg = tmp_path / "config.json"
    host = get_host("claude-desktop")
    assert is_stale(host, path=cfg) is False  # file does not exist
    register_host(host, path=cfg)
    assert is_stale(host, path=cfg) is False  # entry matches canonical
