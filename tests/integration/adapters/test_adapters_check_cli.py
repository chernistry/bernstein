"""Integration tests for ``bernstein adapters check``.

These tests exercise the full Click command surface end-to-end -
from ``CliRunner.invoke`` through the real adapter registry to the
final stdout payload. They don't shell out to a real binary; the
``--help`` capture is mocked at the report module level so the
suite runs offline.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from bernstein.adapters import report as report_mod
from bernstein.adapters.base import CLIAdapter
from bernstein.cli.commands.adapter_cmd import adapters_group
from bernstein.cli.commands.adapters_cmd import (
    adapters_check_cmd,
    adapters_list_status_cmd,
)


class _Stub(CLIAdapter):
    """Bare adapter stub for integration tests."""

    def name(self) -> str:
        return "stub"

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: Any,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
        timeout_seconds: int = 1800,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
    ) -> Any:
        raise NotImplementedError


@pytest.fixture
def empty_contracts(tmp_path: Path) -> Path:
    """Empty contracts directory so all adapters fall into ``skip``."""
    d = tmp_path / "contracts"
    d.mkdir()
    return d


def test_adapters_check_against_real_registry_emits_json() -> None:
    """``bernstein adapters check --format json`` runs end-to-end and parses."""
    runner = CliRunner()
    result = runner.invoke(adapters_check_cmd, ["--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["summary"]["total"] >= 44
    # Every row has the contract surface from the dataclass.
    for row in parsed["adapters"]:
        assert "conformance" in row
        assert row["conformance"] in {"ok", "fail", "skip"}


def test_adapters_check_against_real_registry_table_renders() -> None:
    """Default Rich table prints adapter names and the footer."""
    runner = CliRunner()
    result = runner.invoke(adapters_check_cmd, [])
    assert result.exit_code == 0, result.output
    assert "claude" in result.output
    assert "adapters total" in result.output


def test_adapters_check_single_adapter_form_works() -> None:
    """``bernstein adapters check claude`` filters to a single row."""
    runner = CliRunner()
    result = runner.invoke(adapters_check_cmd, ["claude", "--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["summary"]["total"] == 1
    assert parsed["adapters"][0]["name"] == "claude"


def test_adapters_check_unknown_adapter_returns_exit_two() -> None:
    """An unknown adapter NAME exits with code 2 (Click convention)."""
    runner = CliRunner()
    result = runner.invoke(adapters_check_cmd, ["never-heard-of-it"])
    assert result.exit_code == 2


def test_adapters_check_strict_with_failure_returns_one(empty_contracts: Path, tmp_path: Path) -> None:
    """``--strict`` exits 1 when at least one row is ``fail``."""

    def _iter() -> Any:
        yield "alpha", _Stub

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="usage: alpha\n", stderr="")
    (empty_contracts / "alpha.yaml").write_text(
        "adapter: alpha\nbinary: alpha\ninstall:\n  method: ''\n  spec: ''\n"
        "auth:\n  required_for_help: false\n  required_for_models: false\n  secret_env: ''\n"
        "required_flags:\n  - '--required'\nrequired_subcommands: []\n"
        "expected_models:\n  command: []\n  required_present: []\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    with patch("bernstein.adapters.registry.iter_adapter_specs", _iter):
        with patch.object(report_mod, "CONTRACTS_DIR", empty_contracts):
            with patch.object(report_mod, "_binary_for_adapter", return_value="alpha"):
                with patch.object(report_mod.shutil, "which", return_value="/usr/bin/alpha"):
                    with patch.object(report_mod.subprocess, "run", return_value=completed):
                        result = runner.invoke(adapters_check_cmd, ["--strict", "--format", "json"])
    assert result.exit_code == 1, result.output
    parsed = json.loads(result.output)
    assert parsed["summary"]["fail"] >= 1


def test_adapters_check_strict_zero_when_only_skip(empty_contracts: Path) -> None:
    """Strict mode tolerates ``skip`` (binary missing is expected)."""

    def _iter() -> Any:
        yield "alpha", _Stub

    runner = CliRunner()
    with patch("bernstein.adapters.registry.iter_adapter_specs", _iter):
        with patch.object(report_mod, "CONTRACTS_DIR", empty_contracts):
            result = runner.invoke(adapters_check_cmd, ["--strict", "--format", "json"])
    assert result.exit_code == 0, result.output


def test_adapters_group_lists_check_subcommand() -> None:
    """``bernstein adapters --help`` advertises the ``check`` subcommand."""
    runner = CliRunner()
    result = runner.invoke(adapters_group, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output
    assert "list-status" in result.output


def test_adapters_list_status_cli_runs_against_real_registry() -> None:
    """``list-status`` exits 0 and lists every adapter in JSON mode."""
    runner = CliRunner()
    result = runner.invoke(adapters_list_status_cmd, ["--format", "json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["summary"]["total"] >= 44
    # No row carries a captured version (list-status skips capture).
    for row in parsed["adapters"]:
        assert row["version_string"] is None


def test_adapters_check_invalid_format_rejected() -> None:
    """Click rejects unknown ``--format`` values."""
    runner = CliRunner()
    result = runner.invoke(adapters_check_cmd, ["--format", "yaml"])
    assert result.exit_code != 0
    assert "invalid" in result.output.lower() or "choose from" in result.output.lower()


def test_adapters_check_table_output_has_consistent_columns() -> None:
    """The Rich table always carries adapter / binary / version / caps columns."""
    runner = CliRunner()
    result = runner.invoke(adapters_check_cmd, [])
    assert result.exit_code == 0
    for column in ("adapter", "binary", "version", "caps", "conformance"):
        assert column in result.output, f"missing column header: {column}"
