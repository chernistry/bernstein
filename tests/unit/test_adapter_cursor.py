"""Unit tests for CursorAdapter.spawn().

These tests assert the real ``cursor-agent`` CLI surface (Jan 2026 launch,
v3 / SDK refresh in Apr-May 2026).  The previous adapter shelled a
nonexistent ``cursor agent`` binary with fictional flags
(``--user-data-dir``, ``--add-mcp``); those assertions have been removed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.cursor import CursorAdapter

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_popen_mock(pid: int) -> MagicMock:
    m = MagicMock(spec=subprocess.Popen)
    m.pid = pid
    return m


def _inner_cmd(full_cmd: list[str]) -> list[str]:
    """Extract the actual CLI command after the '--' worker separator."""
    sep = full_cmd.index("--")
    return full_cmd[sep + 1 :]


def _spawn(
    tmp_path: Path,
    *,
    model: str = "claude-sonnet-4-6",
    prompt: str = "do work",
    mcp_config: dict | None = None,
    task_scope: str = "medium",
    env_vars: dict[str, str] | None = None,
) -> tuple[list[str], MagicMock, dict[str, str]]:
    """Invoke spawn() with a mocked Popen, return argv + Popen mock + env."""
    adapter = CursorAdapter()
    proc_mock = _make_popen_mock(pid=500)
    patch_env: dict[str, str] = env_vars or {}
    with patch.dict("os.environ", patch_env, clear=False):
        with patch("bernstein.adapters.cursor.subprocess.Popen", return_value=proc_mock) as popen:
            adapter.spawn(
                prompt=prompt,
                workdir=tmp_path,
                model_config=ModelConfig(model=model, effort="high"),
                session_id="sess-cursor",
                mcp_config=mcp_config,
                task_scope=task_scope,
            )
    cmd: list[str] = popen.call_args.args[0]
    kwargs = popen.call_args.kwargs
    return cmd, popen, kwargs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCursorAdapterSpawn:
    """CursorAdapter.spawn() builds the real cursor-agent argv."""

    def test_wrapped_with_worker(self, tmp_path: Path) -> None:
        cmd, _, _ = _spawn(tmp_path)
        assert cmd[0] == sys.executable
        assert cmd[1:3] == ["-m", "bernstein.core.orchestration.worker"]

    def test_inner_cmd_uses_cursor_agent_binary(self, tmp_path: Path) -> None:
        """Real binary is the single token ``cursor-agent`` - not ``cursor agent``."""
        cmd, _, _ = _spawn(tmp_path)
        inner = _inner_cmd(cmd)
        assert inner[0] == "cursor-agent"
        # Defensive: ensure the broken two-token form is *not* present.
        assert "cursor" not in inner[:1] or inner[0] == "cursor-agent"
        assert "agent" not in inner[1:2]

    def test_print_flag_present(self, tmp_path: Path) -> None:
        """``-p`` is mandatory; without it cursor-agent starts a TTY chat."""
        cmd, _, _ = _spawn(tmp_path)
        inner = _inner_cmd(cmd)
        assert "-p" in inner

    def test_workspace_flag_set_to_workdir(self, tmp_path: Path) -> None:
        cmd, _, _ = _spawn(tmp_path)
        inner = _inner_cmd(cmd)
        assert "--workspace" in inner
        idx = inner.index("--workspace")
        assert inner[idx + 1] == str(tmp_path)

    def test_output_format_stream_json(self, tmp_path: Path) -> None:
        cmd, _, _ = _spawn(tmp_path)
        inner = _inner_cmd(cmd)
        assert "--output-format" in inner
        idx = inner.index("--output-format")
        assert inner[idx + 1] == "stream-json"

    def test_trust_and_approve_mcps_flags(self, tmp_path: Path) -> None:
        """Both flags are required for non-interactive runs."""
        cmd, _, _ = _spawn(tmp_path)
        inner = _inner_cmd(cmd)
        assert "--trust" in inner
        assert "--approve-mcps" in inner

    def test_force_flag_for_default_scope(self, tmp_path: Path) -> None:
        """``--force`` is required to actually apply edits in print mode."""
        cmd, _, _ = _spawn(tmp_path, task_scope="medium")
        inner = _inner_cmd(cmd)
        assert "--force" in inner
        assert "--mode" not in inner  # default is agent mode

    def test_readonly_scope_uses_ask_mode(self, tmp_path: Path) -> None:
        """Read-only tasks run in ask mode (no edits, no mutating tools)."""
        cmd, _, _ = _spawn(tmp_path, task_scope="readonly")
        inner = _inner_cmd(cmd)
        assert "--mode" in inner
        idx = inner.index("--mode")
        assert inner[idx + 1] == "ask"
        assert "--force" not in inner

    def test_model_flag_passed(self, tmp_path: Path) -> None:
        """Model name MUST reach the CLI - previously silently dropped."""
        cmd, _, _ = _spawn(tmp_path, model="claude-opus-4")
        inner = _inner_cmd(cmd)
        assert "--model" in inner
        idx = inner.index("--model")
        assert inner[idx + 1] == "claude-opus-4"

    def test_prompt_via_stdin_not_argv(self, tmp_path: Path) -> None:
        """Prompt is fed via stdin (file redirect), not as a positional arg."""
        unique = "unique-prompt-not-in-argv"
        cmd, _, kwargs = _spawn(tmp_path, prompt=unique)
        inner = _inner_cmd(cmd)
        # The prompt must NOT appear anywhere in argv.
        assert unique not in inner
        # stdin must be a file-like object (not DEVNULL/PIPE/None).
        assert "stdin" in kwargs
        stdin = kwargs["stdin"]
        # The stdin should be a binary file open on the prompt file.
        assert stdin is not subprocess.DEVNULL
        assert stdin is not subprocess.PIPE
        assert stdin is not None
        # Verify the file exists and contains the prompt.
        prompt_file = tmp_path / ".sdd" / "runtime" / "cursor" / "sess-cursor.prompt"
        assert prompt_file.exists()
        assert prompt_file.read_text(encoding="utf-8") == unique

    def test_no_user_data_dir_flag(self, tmp_path: Path) -> None:
        """The bogus ``--user-data-dir`` flag must not appear (regression)."""
        cmd, _, _ = _spawn(tmp_path)
        inner = _inner_cmd(cmd)
        assert "--user-data-dir" not in inner

    def test_no_add_mcp_flag_even_with_mcp_config(self, tmp_path: Path) -> None:
        """The bogus ``--add-mcp`` flag must not appear (regression).

        MCP is configured via the shared ``.cursor/mcp.json`` file instead.
        """
        mcp = {"mcpServers": {"test": {"command": "echo"}}}
        cmd, _, _ = _spawn(tmp_path, mcp_config=mcp)
        inner = _inner_cmd(cmd)
        assert "--add-mcp" not in inner

    def test_mcp_config_written_to_cursor_mcp_json(self, tmp_path: Path) -> None:
        """``mcp_config`` materialises a ``.cursor/mcp.json`` file."""
        mcp = {"mcpServers": {"test": {"command": "echo"}}}
        _spawn(tmp_path, mcp_config=mcp)
        mcp_file = tmp_path / ".cursor" / "mcp.json"
        assert mcp_file.is_file()
        assert json.loads(mcp_file.read_text(encoding="utf-8")) == mcp

    def test_no_mcp_file_without_config(self, tmp_path: Path) -> None:
        _spawn(tmp_path, mcp_config=None)
        assert not (tmp_path / ".cursor" / "mcp.json").exists()

    def test_creates_log_file(self, tmp_path: Path) -> None:
        _spawn(tmp_path)
        log_path = tmp_path / ".sdd" / "runtime" / "sess-cursor.log"
        assert log_path.exists()

    def test_returns_correct_pid(self, tmp_path: Path) -> None:
        adapter = CursorAdapter()
        proc_mock = _make_popen_mock(pid=999)
        with patch("bernstein.adapters.cursor.subprocess.Popen", return_value=proc_mock):
            result = adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="claude-sonnet-4-6", effort="high"),
                session_id="sess-pid",
            )
        assert result.pid == 999

    def test_file_not_found_raises_runtime_error(self, tmp_path: Path) -> None:
        adapter = CursorAdapter()
        with patch("bernstein.adapters.cursor.subprocess.Popen", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="cursor-agent not found in PATH"):
                adapter.spawn(
                    prompt="hello",
                    workdir=tmp_path,
                    model_config=ModelConfig(model="claude-sonnet-4-6", effort="high"),
                    session_id="sess-err",
                )

    def test_cursor_api_key_propagates_into_env(self, tmp_path: Path) -> None:
        """``CURSOR_API_KEY`` is the CI auth path; must reach the spawn env."""
        adapter = CursorAdapter()
        proc_mock = _make_popen_mock(pid=500)
        with patch.dict("os.environ", {"CURSOR_API_KEY": "cur_test_key_123"}, clear=False):
            with patch("bernstein.adapters.cursor.subprocess.Popen", return_value=proc_mock) as popen:
                adapter.spawn(
                    prompt="do work",
                    workdir=tmp_path,
                    model_config=ModelConfig(model="claude-sonnet-4-6", effort="high"),
                    session_id="sess-auth",
                )
        env = popen.call_args.kwargs["env"]
        assert env.get("CURSOR_API_KEY") == "cur_test_key_123"

    def test_env_excludes_unrelated_secrets(self, tmp_path: Path) -> None:
        """Sanity check - only allowlisted vars + CURSOR_API_KEY come through."""
        adapter = CursorAdapter()
        proc_mock = _make_popen_mock(pid=500)
        with patch.dict(
            "os.environ",
            {
                "CURSOR_API_KEY": "cur_xxx",
                "ANTHROPIC_API_KEY": "should_not_leak",
                "DATABASE_URL": "should_not_leak",
            },
            clear=False,
        ):
            with patch("bernstein.adapters.cursor.subprocess.Popen", return_value=proc_mock) as popen:
                adapter.spawn(
                    prompt="do work",
                    workdir=tmp_path,
                    model_config=ModelConfig(model="claude-sonnet-4-6", effort="high"),
                    session_id="sess-iso",
                )
        env = popen.call_args.kwargs["env"]
        assert env.get("CURSOR_API_KEY") == "cur_xxx"
        assert "ANTHROPIC_API_KEY" not in env
        assert "DATABASE_URL" not in env

    def test_name(self) -> None:
        assert CursorAdapter().name() == "Cursor"


class TestCursorAdapterDetectTier:
    """detect_tier() falls through to env-var probe when ~/.cursor is absent."""

    def test_no_creds_returns_none(self, tmp_path: Path) -> None:
        adapter = CursorAdapter()
        with (
            patch("pathlib.Path.home", return_value=tmp_path / "fake-home"),
            patch.dict("os.environ", {}, clear=True),
        ):
            assert adapter.detect_tier() is None

    def test_api_key_alone_authenticates(self, tmp_path: Path) -> None:
        adapter = CursorAdapter()
        with (
            patch("pathlib.Path.home", return_value=tmp_path / "fake-home"),
            patch.dict("os.environ", {"CURSOR_API_KEY": "cur_xxx"}, clear=True),
        ):
            tier = adapter.detect_tier()
        assert tier is not None
        assert tier.is_active

    def test_oauth_dir_alone_authenticates(self, tmp_path: Path) -> None:
        adapter = CursorAdapter()
        fake_home = tmp_path / "fake-home"
        (fake_home / ".cursor").mkdir(parents=True)
        with (
            patch("pathlib.Path.home", return_value=fake_home),
            patch.dict("os.environ", {}, clear=True),
        ):
            tier = adapter.detect_tier()
        assert tier is not None
        assert tier.is_active
