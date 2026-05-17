"""End-to-end lifecycle tests for ``bernstein doctor extended``.

These exercise the full check pipeline (installation -> adapter ->
network -> environment) via :func:`bernstein.cli.doctor.run_all`, with
network and adapter side-effects stubbed so the tests run anywhere.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from click.testing import CliRunner

from bernstein.cli.commands import advanced_cmd
from bernstein.cli.doctor import DoctorResult, run_all
from bernstein.cli.doctor import adapter_checks as adapter_mod
from bernstein.cli.doctor import network_checks as network_mod


def _ok_writer() -> tuple[Any, Any]:
    class _W:
        def close(self) -> None: ...

    return (object(), _W())


def _patch_network_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERNSTEIN_OFFLINE", raising=False)

    async def fake_open(host: str, port: int) -> Any:
        return _ok_writer()

    monkeypatch.setattr(network_mod.asyncio, "open_connection", fake_open)


def _patch_adapter_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_mod.shutil, "which", lambda _name: None)


def test_run_all_offline_only_skip_for_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_OFFLINE", "1")
    _patch_adapter_missing(monkeypatch)

    results = asyncio.run(run_all(adapter_names=["claude"], provider_names=["anthropic"]))
    network_rows = [r for r in results if r.category == "network"]
    assert all(r.status == "skip" for r in network_rows)


def test_run_all_categories_all_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_OFFLINE", "1")  # keep tests offline
    _patch_adapter_missing(monkeypatch)

    results = asyncio.run(run_all(adapter_names=["claude", "codex"]))
    categories = {r.category for r in results}
    assert "adapter" in categories
    assert "network" in categories
    assert "environment" in categories


def test_run_all_adapter_failures_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_OFFLINE", "1")
    _patch_adapter_missing(monkeypatch)

    results = asyncio.run(run_all(adapter_names=["claude"]))
    adapter_rows = [r for r in results if r.category == "adapter"]
    assert any(r.status == "fail" for r in adapter_rows)


def test_doctor_extended_cli_exit_code_zero_when_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # No real network. Offline mode forces network checks to skip, and
    # adapter binaries probably exist for at least one entry.
    monkeypatch.setenv("BERNSTEIN_OFFLINE", "1")

    runner = CliRunner()

    # Force adapter checks to return only ok statuses so the exit code is 0.
    async def fake_adapter_check(*_args: Any, **_kwargs: Any) -> DoctorResult:
        return DoctorResult(name="adapter:claude", category="adapter", status="ok", detail="fake")

    monkeypatch.setattr(adapter_mod, "check_adapter_binary", fake_adapter_check)

    # Patch the legacy installation check to always pass to keep the test deterministic.
    from bernstein.cli import install_check

    monkeypatch.setattr(install_check, "check_installations", lambda: [])

    result = runner.invoke(advanced_cmd.doctor, ["extended", "--adapter", "claude"])
    assert result.exit_code == 0, result.output


def test_doctor_extended_cli_failure_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_OFFLINE", "1")
    monkeypatch.setattr(adapter_mod.shutil, "which", lambda _name: None)

    # Force an installation failure so the overall exit code is 1.
    from bernstein.cli import install_check

    class _Fake:
        def __init__(self) -> None:
            self.name = "Bernstein installations"
            self.ok = False
            self.detail = "synthetic failure"
            self.fix = "do nothing"

    monkeypatch.setattr(install_check, "check_installations", lambda: [_Fake()])

    runner = CliRunner()
    result = runner.invoke(advanced_cmd.doctor, ["extended", "--adapter", "claude", "--provider", "anthropic"])
    assert result.exit_code == 1, result.output


def test_doctor_extended_cli_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setenv("BERNSTEIN_OFFLINE", "1")
    monkeypatch.setattr(adapter_mod.shutil, "which", lambda _name: None)

    from bernstein.cli import install_check

    monkeypatch.setattr(install_check, "check_installations", lambda: [])

    runner = CliRunner()
    result = runner.invoke(
        advanced_cmd.doctor,
        ["extended", "--json", "--adapter", "claude", "--provider", "anthropic"],
    )
    # Exit code is 1 (adapter:claude is fail), but JSON must still parse.
    payload = json.loads(result.output)
    assert "results" in payload
    assert "summary" in payload
    assert payload["summary"]["fail"] >= 1
