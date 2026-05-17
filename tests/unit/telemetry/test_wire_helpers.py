"""Tests for the wire-in helpers used by main.py / worker.py / run_bootstrap."""

from __future__ import annotations

from pathlib import Path

import httpx

from bernstein.core.telemetry import (
    Client,
    ErrorCategory,
    queue_path,
    write_enabled,
)
from bernstein.core.telemetry.wire import (
    FirstRunTimer,
    emit_command_invoked,
    emit_daily_active,
    emit_first_run_completed,
    emit_first_run_started,
    emit_install_completed,
)


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200))


def _enabled_client(tmp_home: Path) -> Client:
    write_enabled(True, home=tmp_home)
    return Client(
        env={},
        home=tmp_home,
        endpoint="http://test.invalid/v1/events",
        http_client=httpx.Client(transport=_ok_transport()),
    )


def test_emit_command_invoked_strips_path(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_command_invoked(name_only="/usr/bin/claude", client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    # Path separators reduced to underscores.
    assert "_usr_bin_claude" in text
    assert "/usr/bin" not in text


def test_emit_command_invoked_strips_args(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_command_invoked(name_only="claude --apikey SECRET", client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert "SECRET" not in text
    assert "--apikey" not in text
    assert "claude" in text


def test_emit_command_invoked_drops_empty_name(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_command_invoked(name_only="", client=c)
    assert not queue_path(home=tmp_home).exists() or queue_path(home=tmp_home).read_text() == ""


def test_emit_first_run_started_clamps_negative_time(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_first_run_started(time_since_install_seconds=-50, client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert '"time_since_install_seconds":0' in text


def test_emit_first_run_completed_ok_clears_error(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_first_run_completed(ok=True, duration_ms=10, error_category=ErrorCategory.AUTH_FAILED, client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert "error_category" not in text


def test_emit_first_run_completed_not_ok_supplies_unknown_default(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_first_run_completed(ok=False, duration_ms=10, client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert '"error_category":"unknown"' in text


def test_emit_install_completed_includes_python_and_os(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_install_completed(client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert "install_completed" in text
    assert "py_version" in text
    assert "install_method" in text


def test_first_run_timer_emits_start_and_complete(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    with FirstRunTimer(time_since_install_seconds=5, client=c):
        pass
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert "first_run_started" in text
    assert "first_run_completed" in text


def test_first_run_timer_records_set_error(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    with FirstRunTimer(client=c) as t:
        t.set_error(ErrorCategory.CONFIG_MISSING)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert '"error_category":"config_missing"' in text


def test_first_run_timer_records_exception(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    try:
        with FirstRunTimer(client=c):
            raise RuntimeError("kaboom")
    except RuntimeError:
        pass
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert '"error_category":"unknown"' in text


def test_emit_daily_active_writes_day(tmp_home: Path) -> None:
    c = _enabled_client(tmp_home)
    emit_daily_active(day_iso="2026-05-17", client=c)
    text = queue_path(home=tmp_home).read_text(encoding="utf-8")
    assert '"day_iso":"2026-05-17"' in text


def test_emit_command_invoked_is_noop_when_disabled(tmp_home: Path) -> None:
    c = Client(env={}, home=tmp_home)
    try:
        emit_command_invoked(name_only="claude", client=c)
    finally:
        c.close()
    assert not queue_path(home=tmp_home).exists()


def test_wire_helpers_never_raise_when_disabled(tmp_home: Path) -> None:
    c = Client(env={}, home=tmp_home)
    try:
        emit_command_invoked(name_only="claude", client=c)
        emit_first_run_started(time_since_install_seconds=1, client=c)
        emit_first_run_completed(ok=True, duration_ms=1, client=c)
        emit_install_completed(client=c)
        emit_daily_active(day_iso="2026-05-17", client=c)
    finally:
        c.close()
