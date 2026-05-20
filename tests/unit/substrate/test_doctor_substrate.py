"""Tests for ``bernstein doctor --substrate``."""

from __future__ import annotations

import json

from click.testing import CliRunner

from bernstein.cli.commands.doctor_cmd import (
    _run_substrate_checks,
    _substrate_status_for,
    doctor_cmd,
)
from bernstein.core.substrate import get_host


def test_substrate_status_for_unsupported_host() -> None:
    """Stubbed hosts report ``unsupported``."""
    row = _substrate_status_for(get_host("codex"))
    assert row["host"] == "codex"
    assert row["state"] == "unsupported"
    assert row["config_path"] is None


def test_substrate_status_for_supported_unregistered(tmp_path, monkeypatch) -> None:
    """A supported host with no entry reports ``not_registered``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    row = _substrate_status_for(get_host("cursor"))
    assert row["state"] == "not_registered"
    assert row["config_path"] is not None


def test_substrate_status_for_registered(tmp_path, monkeypatch) -> None:
    """A registered host reports ``registered``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    from bernstein.core.substrate import register_host

    register_host(get_host("cursor"))
    row = _substrate_status_for(get_host("cursor"))
    assert row["state"] == "registered"


def test_substrate_status_for_stale(tmp_path, monkeypatch) -> None:
    """A host with a divergent entry reports ``stale``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = tmp_path / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "bernstein": {"command": "/old/python", "args": ["-m", "bernstein.mcp"]},
                }
            }
        )
    )

    row = _substrate_status_for(get_host("cursor"))
    assert row["state"] == "stale"


def test_doctor_substrate_json_emits_every_host(tmp_path, monkeypatch) -> None:
    """``doctor --substrate --json`` lists every known host."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(doctor_cmd, ["--substrate", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    hosts = {row["host"] for row in payload["substrate"]}
    assert {"claude-desktop", "claude-code", "cursor", "continue", "cline", "zed", "aider"} <= hosts


def test_doctor_substrate_table_renders(tmp_path, monkeypatch) -> None:
    """The text-mode substrate report mentions known hosts."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(doctor_cmd, ["--substrate"])
    assert result.exit_code == 0, result.output
    assert "cursor" in result.output
    assert "zed" in result.output


def test_run_substrate_checks_returns_one_row_per_host() -> None:
    """``_run_substrate_checks`` returns one row per host in the registry."""
    from bernstein.core.substrate import known_host_names

    rows = _run_substrate_checks()
    assert {row["host"] for row in rows} == set(known_host_names())
