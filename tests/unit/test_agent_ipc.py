"""Focused tests for agent IPC helpers."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from bernstein.core.agent_ipc import (
    _safe_id,
    _stdin_pipes,
    broadcast_message,
    has_stdin_pipe,
    register_stdin_pipe,
    send_message,
    shutdown_all,
    unregister_stdin_pipe,
)


def test_register_and_unregister_stdin_pipe_toggle_registry() -> None:
    """register_stdin_pipe and unregister_stdin_pipe maintain the pipe registry."""
    _stdin_pipes.clear()
    pipe = MagicMock()

    register_stdin_pipe("A-1", pipe)
    assert has_stdin_pipe("A-1") is True

    unregister_stdin_pipe("A-1")
    assert has_stdin_pipe("A-1") is False


def test_send_message_writes_json_payload_to_pipe() -> None:
    """send_message serializes the IPC message as one JSON line to the registered pipe."""
    _stdin_pipes.clear()
    pipe = MagicMock()
    register_stdin_pipe("A-1", pipe)

    assert send_message("A-1", "hello") is True
    pipe.write.assert_called_once()
    pipe.flush.assert_called_once()


def test_send_message_unregisters_pipe_after_broken_pipe() -> None:
    """send_message returns false and drops the pipe when the write fails."""
    _stdin_pipes.clear()
    pipe = MagicMock()
    pipe.write.side_effect = BrokenPipeError("gone")
    register_stdin_pipe("A-1", pipe)

    assert send_message("A-1", "hello") is False
    assert has_stdin_pipe("A-1") is False


def test_broadcast_message_uses_pipe_first_and_file_fallback(tmp_path: Path) -> None:
    """broadcast_message delivers to pipe-backed agents first, then to signal dirs without pipes."""
    _stdin_pipes.clear()
    signals = tmp_path / ".sdd" / "runtime" / "signals"
    (signals / "A-2").mkdir(parents=True)
    pipe = MagicMock()
    register_stdin_pipe("A-1", pipe)

    with patch("bernstein.core.agent_signals.AgentSignalManager") as mock_mgr:
        mock_mgr.return_value.write_command_signal.return_value = True
        result = broadcast_message("wake up", workdir=tmp_path)

    assert result == {"A-1": "pipe", "A-2": "file"}


def test_shutdown_all_wraps_broadcast_with_shutdown_message() -> None:
    """shutdown_all delegates to broadcast_message with a shutdown-prefixed instruction."""
    with patch("bernstein.core.agents.agent_ipc.broadcast_message", return_value={"A-1": "pipe"}) as mock_broadcast:
        result = shutdown_all("maintenance", workdir=Path("/tmp/work"))

    assert result == {"A-1": "pipe"}
    assert "SHUTDOWN: maintenance" in mock_broadcast.call_args.args[0]


def test_safe_id_strips_control_characters() -> None:
    """A session_id carrying CR/LF cannot forge a log line via the %s arg."""
    forged = "agent-1\nFAKE 2026-01-01 admin grant"
    sanitized = _safe_id(forged)
    assert "\n" not in sanitized
    assert "FAKE" in sanitized  # content preserved, separators replaced
    assert sanitized.startswith("agent-1_")


def test_safe_id_truncates_oversized_input() -> None:
    """Attacker-supplied session_id cannot blow up log size unboundedly."""
    assert len(_safe_id("a" * 4096)) == 128


def test_logging_path_uses_sanitized_session_id(caplog) -> None:  # type: ignore[no-untyped-def]
    """Both register/unregister log lines must apply _safe_id."""
    pipe = MagicMock()
    pipe.closed = False
    session = "evil\nINJECTED ADMIN session"
    with caplog.at_level("DEBUG", logger="bernstein.core.agents.agent_ipc"):
        register_stdin_pipe(session, pipe)
        unregister_stdin_pipe(session)
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "INJECTED" in joined  # content kept (sanitized but visible)
    # No record contains a raw newline — every one is single-line.
    assert all("\n" not in rec.getMessage() for rec in caplog.records)
