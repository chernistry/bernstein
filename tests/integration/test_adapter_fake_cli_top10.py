"""Top-6 through top-10 fake-CLI adapter integration tests.

Extends the original top-5 harness (tests/integration/test_adapter_e2e.py)
with five more popular adapters drawn from the registry — Cursor Agent,
AWS Q Developer, JetBrains Junie, Devin / Windsurf Terminal, and the
Mistral ``vibe`` CLI. Each adapter gets:

* a spawn-success test that confirms the worker → fake-CLI chain
  produces the expected tagged stdout, and
* an argv-shape test that recovers the argv the fake_cli saw and
  verifies the adapter's mandatory contract flags survived assembly.

Tests skip on Windows because the harness uses POSIX shell wrappers and
``start_new_session=True`` has no Windows equivalent.
"""

from __future__ import annotations

import platform
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.cursor import CursorAdapter
from bernstein.adapters.devin_terminal import DevinTerminalAdapter
from bernstein.adapters.junie import JunieAdapter
from bernstein.adapters.mistral import MistralAdapter
from bernstein.adapters.q_dev import QDevAdapter

if TYPE_CHECKING:
    from .fake_cli.conftest_adapters import FakeCLIHandle

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="fake-CLI harness uses POSIX shell wrappers",
)


# ---------------------------------------------------------------------------
# Helpers (shared with test_adapter_e2e.py — duplicated to keep this
# file self-contained; the duplication is short and the helpers are
# unlikely to drift)
# ---------------------------------------------------------------------------


def _reap(result: object, *, timeout_s: float = 8.0) -> int:
    """Wait for the spawn result's process to exit and return its code."""
    proc = getattr(result, "proc", None)
    if proc is not None and hasattr(proc, "wait"):
        try:
            return int(proc.wait(timeout=timeout_s))
        except subprocess.TimeoutExpired as exc:  # type: ignore[arg-type]
            raise TimeoutError(f"worker did not exit within {timeout_s}s") from exc
    # Fallback: poll the pid as a liveness probe.
    pid = getattr(result, "pid", 0)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            import os as _os

            _os.kill(pid, 0)
        except ProcessLookupError:
            return 0
        except PermissionError:
            return 0
        time.sleep(0.05)
    raise TimeoutError(f"pid {pid} still alive after {timeout_s}s")


def _wait_for_log(log_path: Path, *, contains: str = "", timeout_s: float = 5.0) -> str:
    """Block until ``log_path`` contains ``contains`` (or is non-empty)."""
    deadline = time.monotonic() + timeout_s
    last_body = ""
    while time.monotonic() < deadline:
        if log_path.exists():
            last_body = log_path.read_text(encoding="utf-8", errors="replace")
            if contains and contains in last_body:
                return last_body
            if not contains and last_body.strip():
                return last_body
        time.sleep(0.05)
    raise TimeoutError(
        f"log {log_path.name} did not contain {contains!r} within {timeout_s}s (body so far: {last_body[:300]!r})"
    )


def _wait_for_dump(dump_path: Path, *, timeout_s: float = 5.0) -> None:
    """Block until ``dump_path`` exists and is non-empty (argv/env dump)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if dump_path.exists() and dump_path.stat().st_size > 0:
            return
        time.sleep(0.05)
    raise TimeoutError(f"dump {dump_path} did not appear within {timeout_s}s")


def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extras: str) -> None:
    """Strip env to the keep-list + the test's named extras."""
    import os

    keep = {"PATH", "TMPDIR", "HOME", "LANG", "LC_ALL", "USER", "SHELL", "TERM"}
    for k in list(os.environ.keys()):
        if k in keep or k.startswith("BERNSTEIN_FAKE_CLI_"):
            continue
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    for key, value in extras.items():
        monkeypatch.setenv(key, value)


def _make_workdir(tmp_path: Path) -> Path:
    """Initialise a minimal git workdir the adapters expect."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    for cmd in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "T"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=workdir, check=True, capture_output=True)
    return workdir


# ---------------------------------------------------------------------------
# Cursor Agent
# ---------------------------------------------------------------------------


class TestCursorEndToEnd:
    """``cursor-agent`` spawn → fake-CLI stream-json output."""

    def test_spawn_succeeds_and_emits_stream_json(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, CURSOR_API_KEY="cur-test")
        workdir = _make_workdir(tmp_path)
        adapter = CursorAdapter()

        result = adapter.spawn(
            prompt="cursor-success",
            workdir=workdir,
            model_config=ModelConfig(model="claude-sonnet-4-6", effort="medium"),
            session_id="cursor-e2e-1",
        )
        assert result.pid > 0
        _reap(result, timeout_s=5.0)
        adapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-cursor-stream-ok")
        assert "fake-cursor-stream-ok" in log_body or "fake-cursor-result" in log_body

    def test_argv_carries_stream_json_and_workspace(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, CURSOR_API_KEY="cur-test")
        workdir = _make_workdir(tmp_path)
        adapter = CursorAdapter()

        result = adapter.spawn(
            prompt="argv-shape",
            workdir=workdir,
            model_config=ModelConfig(model="claude-sonnet-4-6", effort="medium"),
            session_id="cursor-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        adapter.cancel_timeout(result)
        _wait_for_dump(fake_cli_fixture.argv_dump)

        argv = fake_cli_fixture.read_argv()
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--workspace" in argv
        assert "--trust" in argv
        # ``--force`` is required so cursor-agent actually applies edits
        # in print mode (without it the CLI is a silent no-op).
        assert "--force" in argv


# ---------------------------------------------------------------------------
# AWS Q Developer
# ---------------------------------------------------------------------------


class TestQDevEndToEnd:
    """``q chat`` spawn → fake-CLI mixed text/JSON output."""

    def test_spawn_succeeds_and_captures_output(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Q reads its bearer from the on-disk login cache, not env vars,
        # so we simulate a logged-in user by creating the cache dir
        # ``q login`` would have populated.
        _isolated_env(monkeypatch, tmp_path, AWS_REGION="us-east-1")
        (tmp_path / "home" / ".local" / "share" / "amazon-q").mkdir(parents=True, exist_ok=True)
        workdir = _make_workdir(tmp_path)
        adapter = QDevAdapter()

        result = adapter.spawn(
            prompt="q-success",
            workdir=workdir,
            model_config=ModelConfig(model="auto", effort="medium"),
            session_id="q-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        QDevAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-q_dev-output")
        assert "fake-q_dev-output" in log_body

    def test_argv_carries_chat_subcommand(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, AWS_REGION="us-east-1")
        (tmp_path / "home" / ".local" / "share" / "amazon-q").mkdir(parents=True, exist_ok=True)
        workdir = _make_workdir(tmp_path)
        adapter = QDevAdapter()

        result = adapter.spawn(
            prompt="argv-shape-q",
            workdir=workdir,
            model_config=ModelConfig(model="auto", effort="medium"),
            session_id="q-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        QDevAdapter.cancel_timeout(result)
        _wait_for_dump(fake_cli_fixture.argv_dump)

        argv = fake_cli_fixture.read_argv()
        assert "chat" in argv
        assert "--no-interactive" in argv
        assert "--trust-all-tools" in argv


# ---------------------------------------------------------------------------
# JetBrains Junie
# ---------------------------------------------------------------------------


class TestJunieEndToEnd:
    """``junie run --headless`` spawn → fake-CLI structured output."""

    def test_spawn_succeeds_and_captures_result(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, JUNIE_API_KEY="junie-test-x")
        workdir = _make_workdir(tmp_path)
        adapter = JunieAdapter()

        result = adapter.spawn(
            prompt="junie-success",
            workdir=workdir,
            model_config=ModelConfig(model="anthropic/claude-3.5", effort="medium"),
            session_id="junie-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        JunieAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-junie-output")
        assert "fake-junie-output" in log_body

    def test_argv_carries_headless_and_prompt_file(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, JUNIE_API_KEY="junie-test-x")
        workdir = _make_workdir(tmp_path)
        adapter = JunieAdapter()

        result = adapter.spawn(
            prompt="argv-shape-junie",
            workdir=workdir,
            model_config=ModelConfig(model="anthropic/claude-3.5", effort="medium"),
            session_id="junie-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        JunieAdapter.cancel_timeout(result)
        _wait_for_dump(fake_cli_fixture.argv_dump)

        argv = fake_cli_fixture.read_argv()
        assert "run" in argv
        assert "--headless" in argv
        assert "--prompt-file" in argv
        # Junie reads the prompt from a file written by the adapter, so
        # the path must be present and non-empty.
        prompt_idx = argv.index("--prompt-file") + 1
        assert prompt_idx < len(argv)
        assert argv[prompt_idx]


# ---------------------------------------------------------------------------
# Devin / Windsurf Terminal
# ---------------------------------------------------------------------------


class TestDevinTerminalEndToEnd:
    """``devin --print`` spawn → fake-CLI bypass-permission output."""

    def test_spawn_succeeds_and_captures_output(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, DEVIN_API_KEY="dvn-test-x")
        workdir = _make_workdir(tmp_path)
        adapter = DevinTerminalAdapter()

        result = adapter.spawn(
            prompt="devin-success",
            workdir=workdir,
            model_config=ModelConfig(model="claude-sonnet", effort="medium"),
            session_id="devin-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        DevinTerminalAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-devin_terminal-output")
        assert "fake-devin_terminal-output" in log_body

    def test_argv_carries_permission_mode_and_print(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, DEVIN_API_KEY="dvn-test-x")
        workdir = _make_workdir(tmp_path)
        adapter = DevinTerminalAdapter()

        result = adapter.spawn(
            prompt="argv-shape-devin",
            workdir=workdir,
            model_config=ModelConfig(model="claude-sonnet", effort="medium"),
            session_id="devin-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        DevinTerminalAdapter.cancel_timeout(result)
        _wait_for_dump(fake_cli_fixture.argv_dump)

        argv = fake_cli_fixture.read_argv()
        assert "--print" in argv
        assert "--permission-mode" in argv
        assert argv[argv.index("--permission-mode") + 1] == "bypass"


# ---------------------------------------------------------------------------
# Mistral / vibe
# ---------------------------------------------------------------------------


class TestMistralEndToEnd:
    """``vibe --auto-approve`` spawn → fake-CLI conversational output."""

    def test_spawn_succeeds_and_captures_output(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, MISTRAL_API_KEY="mst-test-x")
        workdir = _make_workdir(tmp_path)
        adapter = MistralAdapter()

        result = adapter.spawn(
            prompt="mistral-success",
            workdir=workdir,
            model_config=ModelConfig(model="mistral-large", effort="medium"),
            session_id="mistral-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        MistralAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-mistral-output")
        assert "fake-mistral-output" in log_body

    def test_argv_carries_auto_approve_and_prompt(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, MISTRAL_API_KEY="mst-test-x")
        workdir = _make_workdir(tmp_path)
        adapter = MistralAdapter()

        result = adapter.spawn(
            prompt="argv-shape-mistral",
            workdir=workdir,
            model_config=ModelConfig(model="mistral-large", effort="medium"),
            session_id="mistral-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        MistralAdapter.cancel_timeout(result)
        _wait_for_dump(fake_cli_fixture.argv_dump)

        argv = fake_cli_fixture.read_argv()
        assert "--auto-approve" in argv
        assert "--prompt" in argv
        assert argv[argv.index("--prompt") + 1] == "argv-shape-mistral"


# ---------------------------------------------------------------------------
# Harness self-check — confirms the new wrappers resolve on PATH
# ---------------------------------------------------------------------------


class TestTop10HarnessSelfCheck:
    """Smoke-test the new wrapper bins independently of any adapter."""

    @pytest.mark.parametrize(
        ("binary", "argv_after_binary", "expected_in_stdout"),
        [
            (
                "cursor-agent",
                [
                    "-p",
                    "--workspace",
                    "/tmp/x",
                    "--output-format",
                    "stream-json",
                    "--trust",
                    "--force",
                ],
                "fake-cursor-stream-ok",
            ),
            (
                "q",
                ["chat", "--no-interactive", "--trust-all-tools", "do thing"],
                "fake-q_dev-output",
            ),
            (
                "junie",
                ["run", "--headless", "--prompt-file", "/tmp/p"],
                "fake-junie-output",
            ),
            (
                "devin",
                ["--permission-mode", "bypass", "--print", "do thing"],
                "fake-devin_terminal-output",
            ),
            (
                "vibe",
                ["--auto-approve", "--prompt", "do thing"],
                "fake-mistral-output",
            ),
        ],
        ids=["cursor", "q_dev", "junie", "devin", "mistral"],
    )
    def test_wrapper_resolves_and_emits_profile_payload(
        self,
        fake_cli_fixture: FakeCLIHandle,
        binary: str,
        argv_after_binary: list[str],
        expected_in_stdout: str,
    ) -> None:
        result = subprocess.run(
            [binary, *argv_after_binary],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        assert expected_in_stdout in result.stdout, f"binary {binary!r} produced unexpected stdout: {result.stdout!r}"
