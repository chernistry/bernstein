"""``bernstein telemetry`` subcommand snapshot tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.telemetry_cmd import telemetry_group


def _invoke(args: list[str]) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(telemetry_group, args)
    return result.exit_code, result.output


def test_status_default_off(tmp_home: Path) -> None:
    code, output = _invoke(["status", "--home", str(tmp_home)])
    assert code == 0
    assert "enabled: false" in output
    assert "source: default" in output
    assert "install_id: none" in output


def test_status_after_opt_in(tmp_home: Path) -> None:
    _invoke(["on", "--home", str(tmp_home)])
    code, output = _invoke(["status", "--home", str(tmp_home)])
    assert code == 0
    assert "enabled: true" in output
    assert "source: file" in output
    # install_id should be present (32 hex chars).
    assert "install_id: none" not in output


def test_on_creates_install_id(tmp_home: Path) -> None:
    code, _ = _invoke(["on", "--home", str(tmp_home)])
    assert code == 0
    assert (tmp_home / ".bernstein" / "install-id").exists()


def test_off_removes_install_id(tmp_home: Path) -> None:
    _invoke(["on", "--home", str(tmp_home)])
    _invoke(["off", "--home", str(tmp_home)])
    assert not (tmp_home / ".bernstein" / "install-id").exists()


def test_off_sets_file_false(tmp_home: Path) -> None:
    _invoke(["on", "--home", str(tmp_home)])
    _invoke(["off", "--home", str(tmp_home)])
    _code, output = _invoke(["status", "--home", str(tmp_home)])
    assert "enabled: false" in output
    assert "source: file" in output


def test_export_empty_when_no_queue(tmp_home: Path) -> None:
    code, output = _invoke(["export", "--home", str(tmp_home)])
    assert code == 0
    assert output.strip() == ""


def test_status_snapshot_default(tmp_home: Path) -> None:
    _code, output = _invoke(["status", "--home", str(tmp_home)])
    lines = [line.split(":", 1)[0] for line in output.strip().splitlines()]
    assert lines == [
        "enabled",
        "source",
        "install_id",
        "config_file",
        "install_id_path",
        "queue",
    ]


def test_status_overridden_by_env_off(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _invoke(["on", "--home", str(tmp_home)])
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    _code, output = _invoke(["status", "--home", str(tmp_home)])
    assert "source: do_not_track" in output
    assert "enabled: false" in output
