"""Behavioral tests for the stdin-pipe IPC layer (``agent_ipc``).

Covers pipe registration, real-time message delivery, broken-pipe
recovery, the file-based broadcast fallback, and the log-injection
sanitiser. The module keeps a process-global pipe registry; an autouse
fixture isolates each test from the others by clearing it.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.agents import agent_ipc as ipc


class _FakePipe:
    """A minimal stand-in for an agent's stdin byte stream."""

    def __init__(self, *, broken: bool = False) -> None:
        self.buf = b""
        self.flushes = 0
        self.broken = broken

    def write(self, data: bytes) -> int:
        if self.broken:
            raise OSError("broken pipe")
        self.buf += data
        return len(data)

    def flush(self) -> None:
        if self.broken:
            raise OSError("broken pipe")
        self.flushes += 1


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    """Reset the process-global pipe registry around every test."""
    ipc._stdin_pipes.clear()
    ipc._pipe_write_locks.clear()
    yield
    ipc._stdin_pipes.clear()
    ipc._pipe_write_locks.clear()


# ---------------------------------------------------------------------------
# registration / queries
# ---------------------------------------------------------------------------


def test_register_and_has_pipe() -> None:
    """A registered session reports has_stdin_pipe True."""
    assert ipc.has_stdin_pipe("S1") is False
    ipc.register_stdin_pipe("S1", _FakePipe())
    assert ipc.has_stdin_pipe("S1") is True


def test_register_creates_write_lock() -> None:
    """Registration creates the per-session write lock."""
    ipc.register_stdin_pipe("S1", _FakePipe())
    assert "S1" in ipc._pipe_write_locks


def test_unregister_removes_pipe_but_keeps_lock() -> None:
    """Unregister drops the pipe but deliberately keeps the lock object."""
    ipc.register_stdin_pipe("S1", _FakePipe())
    ipc.unregister_stdin_pipe("S1")
    assert ipc.has_stdin_pipe("S1") is False
    # Lock is intentionally retained so an in-flight send still serialises.
    assert "S1" in ipc._pipe_write_locks


def test_unregister_unknown_is_noop() -> None:
    """Unregistering an unknown session does not raise."""
    ipc.unregister_stdin_pipe("never-registered")
    assert ipc.has_stdin_pipe("never-registered") is False


def test_get_write_lock_creates_on_first_use() -> None:
    """_get_write_lock lazily creates and caches a lock for a new session."""
    assert "fresh" not in ipc._pipe_write_locks
    lock = ipc._get_write_lock("fresh")
    assert ipc._pipe_write_locks["fresh"] is lock
    # A second call returns the same cached lock object.
    assert ipc._get_write_lock("fresh") is lock


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_writes_json_envelope_and_flushes() -> None:
    """A delivered message is a newline-terminated JSON user_message + flush."""
    pipe = _FakePipe()
    ipc.register_stdin_pipe("S1", pipe)
    assert ipc.send_message("S1", "hello there") is True
    assert pipe.flushes == 1
    line, _, rest = pipe.buf.partition(b"\n")
    assert rest == b""  # exactly one trailing newline
    payload = json.loads(line)
    assert payload == {"type": "user_message", "content": "hello there"}


def test_send_message_unknown_session_returns_false() -> None:
    """Sending to a session with no pipe returns False (caller falls back)."""
    assert ipc.send_message("nope", "x") is False


def test_send_message_broken_pipe_unregisters_and_returns_false() -> None:
    """A broken pipe is unregistered and reported as undelivered."""
    pipe = _FakePipe(broken=True)
    ipc.register_stdin_pipe("S1", pipe)
    assert ipc.send_message("S1", "boom") is False
    # The broken pipe is dropped so the next send falls back immediately.
    assert ipc.has_stdin_pipe("S1") is False


def test_send_message_preserves_unicode_content() -> None:
    """Non-ASCII content survives the utf-8 round trip."""
    pipe = _FakePipe()
    ipc.register_stdin_pipe("S1", pipe)
    message = "café 🚀 naïve façade"
    ipc.send_message("S1", message)
    payload = json.loads(pipe.buf.decode("utf-8"))
    assert payload["content"] == message


# ---------------------------------------------------------------------------
# broadcast_message
# ---------------------------------------------------------------------------


def test_broadcast_uses_pipe_for_registered_agents() -> None:
    """Registered agents are reached via pipe and reported as such."""
    ipc.register_stdin_pipe("A", _FakePipe())
    ipc.register_stdin_pipe("B", _FakePipe())
    results = ipc.broadcast_message("all hands", workdir=None)
    assert results == {"A": "pipe", "B": "pipe"}


def test_broadcast_marks_broken_pipe_as_failed() -> None:
    """A broken pipe agent is reported as failed, not pipe."""
    ipc.register_stdin_pipe("A", _FakePipe(broken=True))
    results = ipc.broadcast_message("msg", workdir=None)
    assert results["A"] == "failed"


def test_broadcast_falls_back_to_file_for_unpiped_sessions(tmp_path: Path) -> None:
    """Sessions with a signals dir but no pipe receive a file COMMAND signal."""
    ipc.register_stdin_pipe("PIPE-A", _FakePipe())
    signals = tmp_path / ".sdd" / "runtime" / "signals"
    (signals / "FILE-B").mkdir(parents=True)
    (signals / "FILE-C").mkdir(parents=True)

    results = ipc.broadcast_message("attention", workdir=tmp_path)

    assert results["PIPE-A"] == "pipe"
    assert results["FILE-B"] == "file"
    assert results["FILE-C"] == "file"
    # The file fallback actually writes the COMMAND signal payload.
    cmd_file = signals / "FILE-B" / "COMMAND"
    assert cmd_file.exists()
    assert "attention" in cmd_file.read_text(encoding="utf-8")


def test_broadcast_no_workdir_skips_file_fallback() -> None:
    """Without a workdir, only pipe delivery is attempted."""
    ipc.register_stdin_pipe("PIPE-X", _FakePipe())
    results = ipc.broadcast_message("hi", workdir=None)
    assert results == {"PIPE-X": "pipe"}


def test_broadcast_missing_signals_dir_only_pipes(tmp_path: Path) -> None:
    """A workdir without a signals dir yields only pipe results."""
    ipc.register_stdin_pipe("PIPE-X", _FakePipe())
    results = ipc.broadcast_message("hi", workdir=tmp_path)
    assert results == {"PIPE-X": "pipe"}


# ---------------------------------------------------------------------------
# shutdown_all
# ---------------------------------------------------------------------------


def test_shutdown_all_sends_shutdown_text_via_pipe() -> None:
    """shutdown_all delivers a SHUTDOWN-prefixed instruction over the pipe."""
    pipe = _FakePipe()
    ipc.register_stdin_pipe("Y", pipe)
    results = ipc.shutdown_all(reason="operator stop", workdir=None)
    assert results == {"Y": "pipe"}
    payload = json.loads(pipe.buf.decode("utf-8"))
    assert payload["content"].startswith("SHUTDOWN: operator stop")
    assert "exit immediately" in payload["content"]


def test_shutdown_all_file_fallback(tmp_path: Path) -> None:
    """shutdown_all also reaches file-only sessions with a COMMAND signal."""
    signals = tmp_path / ".sdd" / "runtime" / "signals"
    (signals / "FILE-Z").mkdir(parents=True)
    results = ipc.shutdown_all(reason="budget hit", workdir=tmp_path)
    assert results["FILE-Z"] == "file"
    cmd = (signals / "FILE-Z" / "COMMAND").read_text(encoding="utf-8")
    assert "SHUTDOWN: budget hit" in cmd


# ---------------------------------------------------------------------------
# _safe_id sanitiser
# ---------------------------------------------------------------------------


def test_safe_id_replaces_control_chars() -> None:
    """CR/LF/TAB/ESC are replaced with underscores to defeat log injection."""
    assert ipc._safe_id("a\nb\rc\td\x1be") == "a_b_c_d_e"


def test_safe_id_truncates_to_max_len() -> None:
    """Oversize ids are truncated to the documented cap."""
    out = ipc._safe_id("x" * 500)
    assert len(out) == ipc._SAFE_ID_MAX_LEN


def test_safe_id_passes_through_clean_id() -> None:
    """A clean uuid-ish id is returned unchanged."""
    sid = "agent-1234-abcd"
    assert ipc._safe_id(sid) == sid
