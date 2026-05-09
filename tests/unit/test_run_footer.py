"""Tests for the post-run footer line.

Covers the contract from the OSS-visibility ticket:

* one-line, dim, tty-only output at end of ``bernstein run``
* honoured opt-outs: ``BERNSTEIN_DISABLE_FOOTER``, ``BERNSTEIN_NO_BANNER``,
  ``BERNSTEIN_QUIET``, ``NO_COLOR``, ``BERNSTEIN_OUTPUT=json``
* never printed to non-tty stderr (pipe / file redirect)
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bernstein.cli.run import _emit_run_footer, _footer_suppressed


def _make_tty_console() -> MagicMock:
    """Return a mock Console that pretends it is attached to a tty."""
    console = MagicMock()
    console.is_terminal = True
    return console


class TestFooterSuppressed:
    """Unit tests for the suppression rules."""

    def test_default_tty_not_suppressed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Strip every relevant env var, fake stderr.isatty() True
        for var in (
            "BERNSTEIN_DISABLE_FOOTER",
            "BERNSTEIN_NO_BANNER",
            "BERNSTEIN_QUIET",
            "BERNSTEIN_OUTPUT",
            "NO_COLOR",
        ):
            monkeypatch.delenv(var, raising=False)

        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            assert _footer_suppressed() is False

    def test_disable_footer_env_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_DISABLE_FOOTER", "1")
        assert _footer_suppressed() is True

    def test_legacy_no_banner_env_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_DISABLE_FOOTER", raising=False)
        monkeypatch.setenv("BERNSTEIN_NO_BANNER", "true")
        assert _footer_suppressed() is True

    def test_quiet_env_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_DISABLE_FOOTER", raising=False)
        monkeypatch.setenv("BERNSTEIN_QUIET", "yes")
        assert _footer_suppressed() is True

    def test_no_color_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BERNSTEIN_DISABLE_FOOTER", raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        assert _footer_suppressed() is True

    def test_json_output_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("BERNSTEIN_DISABLE_FOOTER", "BERNSTEIN_NO_BANNER", "BERNSTEIN_QUIET", "NO_COLOR"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("BERNSTEIN_OUTPUT", "json")
        assert _footer_suppressed() is True

    def test_non_tty_stderr_suppresses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "BERNSTEIN_DISABLE_FOOTER",
            "BERNSTEIN_NO_BANNER",
            "BERNSTEIN_QUIET",
            "BERNSTEIN_OUTPUT",
            "NO_COLOR",
        ):
            monkeypatch.delenv(var, raising=False)

        # Pipe / file redirect → isatty() returns False
        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = False
            assert _footer_suppressed() is True


class TestEmitFooter:
    """Behavioural tests for the renderer."""

    def test_prints_when_console_is_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "BERNSTEIN_DISABLE_FOOTER",
            "BERNSTEIN_NO_BANNER",
            "BERNSTEIN_QUIET",
            "BERNSTEIN_OUTPUT",
            "NO_COLOR",
        ):
            monkeypatch.delenv(var, raising=False)

        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            con = _make_tty_console()
            _emit_run_footer(console=con)
        assert con.print.call_count == 1
        # The first positional argument should be a Rich Text instance whose
        # plaintext contains the project URL.
        rendered = con.print.call_args.args[0]
        assert "bernstein.run" in rendered.plain
        assert "✓ run signed" in rendered.plain

    def test_skips_when_suppressed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_DISABLE_FOOTER", "1")
        con = _make_tty_console()
        _emit_run_footer(console=con)
        con.print.assert_not_called()

    def test_skips_when_console_is_not_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "BERNSTEIN_DISABLE_FOOTER",
            "BERNSTEIN_NO_BANNER",
            "BERNSTEIN_QUIET",
            "BERNSTEIN_OUTPUT",
            "NO_COLOR",
        ):
            monkeypatch.delenv(var, raising=False)

        with patch("sys.stderr") as mock_stderr:
            mock_stderr.isatty.return_value = True
            con = MagicMock()
            con.is_terminal = False
            _emit_run_footer(console=con)
        con.print.assert_not_called()

    def test_default_console_writes_to_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no console is passed, output goes to stderr (not stdout)."""
        for var in (
            "BERNSTEIN_DISABLE_FOOTER",
            "BERNSTEIN_NO_BANNER",
            "BERNSTEIN_QUIET",
            "BERNSTEIN_OUTPUT",
            "NO_COLOR",
        ):
            monkeypatch.delenv(var, raising=False)

        captured: dict[str, Any] = {}

        class _StubConsole:
            def __init__(self, **kwargs: Any) -> None:
                captured["init_kwargs"] = kwargs
                self.is_terminal = True

            def print(self, *args: Any, **kwargs: Any) -> None:
                captured["printed"] = args[0] if args else None

        with patch("sys.stderr") as mock_stderr, patch("bernstein.cli.run.Console", _StubConsole):
            mock_stderr.isatty.return_value = True
            _emit_run_footer()
        assert captured.get("init_kwargs", {}).get("stderr") is True
        assert captured.get("printed") is not None
