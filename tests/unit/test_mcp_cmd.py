"""Tests for the MCP CLI group and marketplace helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from bernstein.cli.mcp_cmd import mcp_server
from bernstein.core.mcp_registry import load_catalog_entries


def test_mcp_command_defaults_to_stdio_server() -> None:
    runner = CliRunner()

    with patch("bernstein.mcp.server.run_stdio") as mock_stdio:
        result = runner.invoke(mcp_server, [])

    assert result.exit_code == 0
    mock_stdio.assert_called_once()


def test_mcp_command_http_mode_runs_sse_server() -> None:
    runner = CliRunner()

    with patch("bernstein.mcp.server.run_sse") as mock_sse:
        result = runner.invoke(mcp_server, ["--transport", "http", "--host", "0.0.0.0", "--port", "9999"])

    assert result.exit_code == 0
    mock_sse.assert_called_once_with(server_url="http://localhost:8052", host="0.0.0.0", port=9999)


def test_mcp_list_shows_bundled_marketplace_entries() -> None:
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(mcp_server, ["list"])

    assert result.exit_code == 0
    assert "filesystem" in result.output
    assert "github" in result.output
    assert "available" in result.output


def test_mcp_install_creates_catalog_and_is_idempotent() -> None:
    runner = CliRunner()

    with runner.isolated_filesystem():
        first = runner.invoke(mcp_server, ["install", "filesystem"])
        catalog_path = Path(".sdd/config/mcp_servers.yaml")
        catalog_exists = catalog_path.exists()
        loaded_once = load_catalog_entries(catalog_path)

        second = runner.invoke(mcp_server, ["install", "filesystem"])
        loaded_twice = load_catalog_entries(catalog_path)

    assert first.exit_code == 0
    assert catalog_exists is True
    assert [entry.name for entry in loaded_once] == ["filesystem"]
    assert second.exit_code == 0
    assert [entry.name for entry in loaded_twice] == ["filesystem"]
    assert "Already installed" in second.output
