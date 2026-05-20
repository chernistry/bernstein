"""Tests for the ``bernstein desktop-register`` CLI command."""

from __future__ import annotations

import json

from click.testing import CliRunner

from bernstein.cli.commands.desktop_register_cmd import desktop_register_cmd
from bernstein.core.substrate.host_registry import SERVER_ID


def test_list_shows_supported_and_stubbed() -> None:
    """``--list`` shows both supported hosts and at least one stub."""
    result = CliRunner().invoke(desktop_register_cmd, ["--list"])
    assert result.exit_code == 0, result.output
    assert "claude-desktop" in result.output
    assert "claude-code" in result.output
    assert "supported" in result.output
    assert "stubbed" in result.output


def test_list_json_is_parseable() -> None:
    """``--list --json`` emits parseable JSON with the documented schema."""
    result = CliRunner().invoke(desktop_register_cmd, ["--list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_name = {row["host"]: row for row in payload["hosts"]}
    assert by_name["claude-desktop"]["status"] == "supported"
    assert by_name["cursor"]["status"] == "stubbed"
    for row in payload["hosts"]:
        assert {"host", "status", "scope", "config_path", "registered", "notes"} <= row.keys()


def test_register_claude_desktop_via_cli(tmp_path, monkeypatch) -> None:
    """``--host claude-desktop`` writes the entry into the resolved config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(desktop_register_cmd, ["--host", "claude-desktop", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["action"] == "registered"

    cfg = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if not cfg.exists():
        # Non-macOS CI resolves to the XDG path instead.
        cfg = home / ".config" / "Claude" / "claude_desktop_config.json"
    data = json.loads(cfg.read_text())
    assert SERVER_ID in data["mcpServers"]


def test_register_unknown_host_errors() -> None:
    """An unknown ``--host`` is a usage error listing valid hosts."""
    result = CliRunner().invoke(desktop_register_cmd, ["--host", "bogus"])
    assert result.exit_code != 0
    assert "unknown host" in result.output


def test_register_stubbed_host_exits_nonzero() -> None:
    """A stubbed host reports not-implemented and exits non-zero."""
    result = CliRunner().invoke(desktop_register_cmd, ["--host", "cursor"])
    assert result.exit_code == 1
    assert "not yet supported" in result.output


def test_no_args_prints_hint_and_exits_two() -> None:
    """Bare invocation nudges toward --host/--list and exits with code 2."""
    result = CliRunner().invoke(desktop_register_cmd, [])
    assert result.exit_code == 2
    assert "--host" in result.output
