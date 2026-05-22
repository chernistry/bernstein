"""Tests for MCP server config validation (startup checks).

Covers ``mcp_config_validator`` without touching the network:

* ``check_command_exists`` - stdio command present / absent / missing.
* ``check_env_vars`` - ``${VAR}`` reference resolution against the env.
* ``check_url_reachable`` - stdio skip, missing-url error, and the
  URL-scheme rejection path (deterministic, no socket).
* ``validate_mcp_configs`` aggregation across multiple configs.
* ``McpConfigError.__str__`` formatting.

``shutil.which`` and ``os.environ`` are stubbed so the command/env
checks are deterministic; URL reachability is exercised only on paths
that never open a socket.
"""

from __future__ import annotations

import pytest

from bernstein.core.protocols.mcp.mcp_config_validator import (
    McpConfigError,
    check_command_exists,
    check_env_vars,
    check_url_reachable,
    validate_mcp_configs,
)
from bernstein.core.protocols.mcp.mcp_manager import MCPServerConfig


def _stdio(name: str = "srv", command: list[str] | None = None, env: dict[str, str] | None = None) -> MCPServerConfig:
    return MCPServerConfig(name=name, command=command if command is not None else ["mytool"], env=env or {})


def _remote(
    name: str = "srv",
    *,
    transport: str = "streamable_http",
    url: str = "",
    env: dict[str, str] | None = None,
) -> MCPServerConfig:
    return MCPServerConfig(name=name, command=[], url=url, transport=transport, env=env or {})


class TestCheckCommandExists:
    def test_non_stdio_returns_none(self) -> None:
        assert check_command_exists(_remote(transport="sse")) is None

    def test_missing_command_flagged(self) -> None:
        err = check_command_exists(_stdio(command=[]))
        assert err is not None
        assert err.check == "command_missing"

    def test_command_not_in_path_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "bernstein.core.protocols.mcp.mcp_config_validator.shutil.which",
            lambda _exe: None,
        )
        err = check_command_exists(_stdio(command=["definitely-not-real-binary"]))
        assert err is not None
        assert err.check == "command_not_found"
        assert "definitely-not-real-binary" in err.message

    def test_command_present_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "bernstein.core.protocols.mcp.mcp_config_validator.shutil.which",
            lambda _exe: "/usr/bin/mytool",
        )
        assert check_command_exists(_stdio(command=["mytool"])) is None


class TestCheckEnvVars:
    def test_set_reference_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret")
        errs = check_env_vars(_stdio(env={"TOKEN": "${MY_TOKEN}"}))
        assert errs == []

    def test_missing_reference_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING_VAR", raising=False)
        errs = check_env_vars(_stdio(env={"TOKEN": "${MISSING_VAR}"}))
        assert len(errs) == 1
        assert errs[0].check == "env_var_missing"
        assert "MISSING_VAR" in errs[0].message

    def test_literal_value_not_treated_as_reference(self) -> None:
        # plain literals are never checked against the environment.
        assert check_env_vars(_stdio(env={"MODE": "production"})) == []

    def test_multiple_missing_refs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("A_VAR", raising=False)
        monkeypatch.delenv("B_VAR", raising=False)
        errs = check_env_vars(_stdio(env={"a": "${A_VAR}", "b": "${B_VAR}"}))
        assert len(errs) == 2


class TestCheckUrlReachable:
    def test_stdio_transport_skipped(self) -> None:
        assert check_url_reachable(_stdio()) is None

    def test_missing_url_flagged(self) -> None:
        err = check_url_reachable(_remote(url=""))
        assert err is not None
        assert err.check == "url_missing"

    def test_disallowed_scheme_flagged(self) -> None:
        # A non-http(s) scheme is rejected by the allowlist before any
        # socket is opened, so this stays offline + deterministic.
        err = check_url_reachable(_remote(url="ftp://example.com/feed"))
        assert err is not None
        assert err.check == "url_scheme"


class TestValidateMcpConfigs:
    def test_clean_stdio_config_no_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "bernstein.core.protocols.mcp.mcp_config_validator.shutil.which",
            lambda _exe: "/usr/bin/mytool",
        )
        errors = validate_mcp_configs([_stdio(command=["mytool"])], check_urls=False)
        assert errors == []

    def test_aggregates_errors_across_configs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "bernstein.core.protocols.mcp.mcp_config_validator.shutil.which",
            lambda _exe: None,
        )
        monkeypatch.delenv("ABSENT", raising=False)
        configs = [
            _stdio(name="a", command=["ghost-binary"]),
            _stdio(name="b", command=["mytool"], env={"T": "${ABSENT}"}),
        ]
        errors = validate_mcp_configs(configs, check_urls=False)
        names = {e.server_name for e in errors}
        # a has a missing command; b has a missing env var.
        assert "a" in names
        assert "b" in names
        assert len(errors) >= 2

    def test_url_check_skipped_when_disabled(self) -> None:
        # A remote config with a missing URL would normally error, but
        # check_urls=False suppresses the URL check entirely.
        errors = validate_mcp_configs([_remote(url="")], check_urls=False)
        assert errors == []

    def test_url_check_runs_when_enabled(self) -> None:
        # Missing URL on a remote transport surfaces with check_urls=True.
        errors = validate_mcp_configs([_remote(name="r", url="")], check_urls=True)
        assert any(e.check == "url_missing" for e in errors)


class TestMcpConfigError:
    def test_str_format(self) -> None:
        err = McpConfigError(server_name="gh", check="command_not_found", message="boom")
        assert str(err) == "[gh] command_not_found: boom"
