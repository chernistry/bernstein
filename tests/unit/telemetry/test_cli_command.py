"""``bernstein telemetry`` subcommand snapshot tests."""

from __future__ import annotations

from collections.abc import Mapping
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
        "share_with_maintainer",
        "share_source",
        "share_config_file",
        "share_endpoint_configured",
        "dsn",
    ]


def test_status_reports_share_endpoint_presence_without_printing_url(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status exposes maintainer-share endpoint presence without leaking its value."""
    from bernstein.core.telemetry.share import SHARE_ENDPOINT_ENV

    monkeypatch.setenv(SHARE_ENDPOINT_ENV, "https://maintainer.example.test/v1/events")

    code, output = _invoke(["status", "--home", str(tmp_home)])

    assert code == 0
    assert "share_endpoint_configured: true" in output
    assert "maintainer.example.test" not in output


def test_status_overridden_by_env_off(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _invoke(["on", "--home", str(tmp_home)])
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    _code, output = _invoke(["status", "--home", str(tmp_home)])
    assert "source: do_not_track" in output
    assert "enabled: false" in output


def test_enable_requires_confirmation_and_writes_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enable prints schema + redaction, confirms, then writes the TOML."""
    xdg = tmp_path / "xdg-config"
    xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("BERNSTEIN_TELEMETRY_SHARE", raising=False)

    runner = CliRunner()
    result = runner.invoke(
        telemetry_group,
        ["enable", "--share-with-maintainer"],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert "Event schema" in result.output
    assert "Redaction rules" in result.output
    assert "share_with_maintainer = true" in result.output

    toml_path = xdg / "bernstein" / "telemetry.toml"
    assert toml_path.exists()
    body = toml_path.read_text(encoding="utf-8")
    assert "share_with_maintainer = true" in body


def test_enable_declined_does_not_write_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declining the confirmation leaves the consent file untouched."""
    xdg = tmp_path / "xdg-config"
    xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("BERNSTEIN_TELEMETRY_SHARE", raising=False)

    runner = CliRunner()
    result = runner.invoke(
        telemetry_group,
        ["enable", "--share-with-maintainer"],
        input="n\n",
    )
    assert result.exit_code == 0, result.output
    assert "consent declined" in result.output
    assert not (xdg / "bernstein" / "telemetry.toml").exists()


def test_disable_writes_false_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disable persists share_with_maintainer = false."""
    xdg = tmp_path / "xdg-config"
    xdg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("BERNSTEIN_TELEMETRY_SHARE", raising=False)

    runner = CliRunner()
    result = runner.invoke(telemetry_group, ["disable"])
    assert result.exit_code == 0, result.output
    toml_path = xdg / "bernstein" / "telemetry.toml"
    assert toml_path.exists()
    assert "share_with_maintainer = false" in toml_path.read_text(encoding="utf-8")


def test_tail_empty_message_when_buffer_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tail prints a helpful message when no events have been buffered."""
    from bernstein.core.observability import sidechannel

    sidechannel.clear_preview()
    runner = CliRunner()
    result = runner.invoke(telemetry_group, ["tail"])
    assert result.exit_code == 0, result.output
    assert "no events buffered" in result.output


def test_tail_prints_buffered_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tail prints the rendered events from the preview ring buffer."""
    from bernstein.core.observability import sidechannel

    sidechannel.clear_preview()
    monkeypatch.delenv(sidechannel.DSN_ENV, raising=False)
    # ``emit`` records the rendered payload in the preview buffer even when
    # the sink is the Null sink, which is the operator-audit guarantee.
    sidechannel.emit(category="run", message="first")
    sidechannel.emit(category="run", message="second")
    runner = CliRunner()
    result = runner.invoke(telemetry_group, ["tail", "-n", "5"])
    assert result.exit_code == 0, result.output
    assert "first" in result.output
    assert "second" in result.output


def test_probe_without_dsn_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.observability import sidechannel

    monkeypatch.delenv(sidechannel.DSN_ENV, raising=False)
    code, output = _invoke(["probe"])
    assert code == 0
    assert "is not set" in output
    assert "nothing emitted" in output


def test_probe_with_dsn_emits_synthetic_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from bernstein.core.observability import sidechannel

    sent: list[dict[str, object]] = []

    class _Transport:
        def send(self, payload: dict[str, object]) -> bool:
            sent.append(payload)
            return True

    monkeypatch.setenv(sidechannel.DSN_ENV, "https://k@host/1")

    real_build = sidechannel.build_sidechannel

    def build_with_transport(
        *,
        env: Mapping[str, str] | None = None,
        transport: object | None = None,
    ) -> sidechannel.SideChannelSink:
        _ = transport
        return real_build(env=env, transport=_Transport())

    monkeypatch.setattr(
        sidechannel,
        "build_sidechannel",
        build_with_transport,
    )

    code, output = _invoke(["probe", "--message", "hello probe"])
    assert code == 0
    assert "queued for delivery" in output
    assert sent and sent[0]["message"] == "hello probe"
    assert sent[0]["logger"] == "bernstein.probe"
