"""Tests for the deterministic surface of ``bernstein.cli.dashboard_app``.

The dashboard is a Textual TUI, so most of it only runs under an event loop.
These tests target the parts that are observable without driving the UI:

  * ``_format_boot_log_line`` - boot-log parsing / level colouring / escaping
  * ``BernsteinApp.__init__`` - default state + activity-log-file wiring
  * ``BernsteinApp._write_activity`` - markup-stripped file output

Construction is done bare (no ``run()``), which exercises ``__init__`` without
spinning up the Textual driver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.cli.dashboard_app import BernsteinApp, _format_boot_log_line

# ---------------------------------------------------------------------------
# _format_boot_log_line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_format_boot_log_line_skips_blank(blank: str) -> None:
    assert _format_boot_log_line(blank) is None


def test_format_boot_log_line_skips_http_request_noise() -> None:
    """HTTP-client request logs are noise and are dropped."""
    assert _format_boot_log_line("2026-05-22 12:00:00 INFO HTTP Request: GET /x") is None


def test_format_boot_log_line_skips_too_few_fields() -> None:
    """A line without the date/time/level/message shape is skipped."""
    assert _format_boot_log_line("only two") is None
    assert _format_boot_log_line("date time level") is None


def test_format_boot_log_line_info_default_template() -> None:
    out = _format_boot_log_line("2026-05-22 12:00:00,123 INFO bernstein: started up")
    assert out is not None
    # Time is taken (comma-stripped), default OK template, module prefix dropped.
    assert "12:00:00" in out
    assert "OK" in out
    assert "started up" in out
    assert "bernstein:" not in out


def test_format_boot_log_line_error_level() -> None:
    out = _format_boot_log_line("2026-05-22 12:00:00,123 ERROR something broke")
    assert out is not None
    assert "ERR" in out
    assert "something broke" in out
    assert "[red]" in out


def test_format_boot_log_line_warning_level() -> None:
    out = _format_boot_log_line("2026-05-22 12:00:00 WARNING heads up")
    assert out is not None
    assert "WARN" in out
    assert "[yellow]" in out


def test_format_boot_log_line_escapes_open_bracket() -> None:
    """A '[' in the message is escaped so Rich does not treat it as markup."""
    out = _format_boot_log_line("2026-05-22 12:00:00 INFO mod: value [x] here")
    assert out is not None
    assert r"\[x]" in out


def test_format_boot_log_line_truncates_long_message() -> None:
    long_msg = "z" * 200
    out = _format_boot_log_line(f"2026-05-22 12:00:00 INFO mod: {long_msg}")
    assert out is not None
    # The message component is capped at 80 chars.
    assert "z" * 80 in out
    assert "z" * 81 not in out


# ---------------------------------------------------------------------------
# BernsteinApp.__init__ - default state
# ---------------------------------------------------------------------------


def test_app_init_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERNSTEIN_ACTIVITY_LOG", raising=False)
    app = BernsteinApp()
    assert app.title == "BERNSTEIN"
    assert app.sub_title == "Agent Orchestra"
    assert app._activity_visible is True
    assert app._expert_mode is False
    assert app._evolve is False
    # Bounded ring buffers for sparklines.
    assert app._history.maxlen == 60
    assert app._cost_history.maxlen == 10
    # No activity-log file when the env var is unset.
    assert app._activity_log_file is None


def test_app_init_opens_activity_log_and_makes_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "nested" / "activity.log"
    monkeypatch.setenv("BERNSTEIN_ACTIVITY_LOG", str(log_path))
    app = BernsteinApp()
    try:
        assert app._activity_log_file is not None
        # The parent dir is created on demand.
        assert log_path.parent.is_dir()
    finally:
        if app._activity_log_file is not None:
            app._activity_log_file.close()


def test_app_init_bad_activity_log_path_does_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An un-openable log path is tolerated (logged), not fatal."""
    # Point the log at a path whose parent is a regular file - mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setenv("BERNSTEIN_ACTIVITY_LOG", str(blocker / "child" / "activity.log"))
    app = BernsteinApp()
    # Construction succeeds; the file simply stays unset.
    assert app._activity_log_file is None


# ---------------------------------------------------------------------------
# _write_activity - file output (markup-stripped)
# ---------------------------------------------------------------------------


def test_write_activity_strips_markup_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When an activity-log file is configured, lines are written plain.

    ``_write_activity`` also tries to write to the RichLog widget, but that
    query fails silently on an un-mounted app (it is wrapped in suppress),
    so the file write still happens.
    """
    log_path = tmp_path / "activity.log"
    monkeypatch.setenv("BERNSTEIN_ACTIVITY_LOG", str(log_path))
    app = BernsteinApp()
    try:
        app._write_activity("backend", "building the parser")
    finally:
        if app._activity_log_file is not None:
            app._activity_log_file.close()

    content = log_path.read_text()
    # Role surfaces in the line and Rich markup tags are stripped out.
    assert "BACKEND" in content.upper()
    assert "building the parser" in content
    assert "[/" not in content
