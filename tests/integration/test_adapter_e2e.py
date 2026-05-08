"""End-to-end adapter integration tests using the fake-CLI harness.

These tests cover the full Popen → output capture → exit code path for
the top-5 adapters (Claude, Codex, Gemini, Aider, Ollama).  They differ
from ``tests/unit/test_adapter_*.py`` in that they DO NOT mock
``subprocess.Popen`` — instead they prepend a tempdir of fake-CLI
wrappers onto ``PATH`` and let the adapter spawn a real subprocess that
behaves like the upstream tool.

Coverage matrix per adapter (15 cases minimum):

* spawn-success — the adapter assembles argv, captures stdout, returns
  ``SpawnResult`` with the live PID; the fake records the argv/env it
  saw on disk so the assertions can inspect them.
* exit-code propagation — error mode triggers ``SpawnError`` (probed
  adapters: claude, codex, gemini) or surfaces via ``proc.wait()``
  (aider, ollama, which don't probe).
* env isolation — secret-bearing master keys are stripped, scoped keys
  pass through, and the spawn log doesn't leak the master secret bytes.

Bug regressions found while writing this harness are documented in the
PR body.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.aider import AiderAdapter
from bernstein.adapters.base import SpawnError
from bernstein.adapters.claude import ClaudeCodeAdapter
from bernstein.adapters.codex import CodexAdapter
from bernstein.adapters.gemini import GeminiAdapter
from bernstein.adapters.ollama import OllamaAdapter

if TYPE_CHECKING:
    from .fake_cli.conftest_adapters import FakeCLIHandle

# Skip the whole module on Windows — the harness uses POSIX shell wrappers
# and bernstein-worker uses ``start_new_session=True`` which has no Windows
# equivalent.  Adapter unit tests still cover the argv/env logic on Windows.
pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="fake-CLI harness uses POSIX shell wrappers",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reap(result: object, *, timeout_s: float = 8.0) -> int:
    """Wait for the SpawnResult's process to exit and return its code.

    Prefers :meth:`subprocess.Popen.wait` when the adapter wired ``proc``
    into the :class:`SpawnResult` (codex/gemini/claude/aider/ollama all
    do after the audit-this-PR fix); falls back to ``os.kill(pid, 0)``
    polling for any future adapter that hasn't been audited yet.

    Args:
        result: A :class:`bernstein.adapters.base.SpawnResult` instance.
        timeout_s: Hard deadline before giving up and raising
            :class:`TimeoutError`.

    Returns:
        Exit code of the worker process (``0`` when the process was
        already reaped under us, e.g. by a sibling thread).
    """
    proc = getattr(result, "proc", None)
    pid = getattr(result, "pid", 0)
    if proc is not None and hasattr(proc, "wait"):
        try:
            return int(proc.wait(timeout=timeout_s))
        except subprocess.TimeoutExpired as exc:  # type: ignore[arg-type]
            raise TimeoutError(f"worker pid {pid} did not exit within {timeout_s}s") from exc
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return 0
        except PermissionError:
            return 0
        time.sleep(0.05)
    raise TimeoutError(f"pid {pid} still alive after {timeout_s}s")


def _wait_for_log(log_path: Path, *, contains: str = "", timeout_s: float = 5.0) -> str:
    """Block until ``log_path`` contains ``contains`` (or the file is non-empty).

    Returns the full log body once the predicate is satisfied; raises
    :class:`TimeoutError` if the deadline passes — failure surfaces the
    actual log contents for easier debugging.
    """
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
    """Block until ``dump_path`` exists (argv/env dump file)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if dump_path.exists() and dump_path.stat().st_size > 0:
            return
        time.sleep(0.05)
    raise TimeoutError(f"dump {dump_path} did not appear within {timeout_s}s")


def _model_for(adapter_name: str) -> ModelConfig:
    """Map adapter → a sensible model name for spawn calls."""
    return {
        "claude": ModelConfig(model="sonnet", effort="medium"),
        "codex": ModelConfig(model="gpt-5.5-mini", effort="medium"),
        "gemini": ModelConfig(model="gemini-3-flash", effort="medium"),
        "aider": ModelConfig(model="sonnet", effort="medium"),
        "ollama": ModelConfig(model="haiku", effort="medium"),
    }[adapter_name]


def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extras: str) -> None:
    """Strip env to the minimum that the adapter spawn path needs.

    Tests that assert on env isolation set the master keys here and then
    inspect the dumped agent env to confirm filtering.
    """
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
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workdir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=workdir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=workdir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=workdir,
        check=True,
        capture_output=True,
    )
    return workdir


# ---------------------------------------------------------------------------
# Aider adapter (simplest — single Popen, no probe)
# ---------------------------------------------------------------------------


class TestAiderEndToEnd:
    """AiderAdapter spawn → fake-CLI execution → log capture."""

    def test_spawn_succeeds_and_captures_output(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test-deadbeef")
        workdir = _make_workdir(tmp_path)
        adapter = AiderAdapter()

        result = adapter.spawn(
            prompt="fix the bug",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-e2e-1",
        )

        assert result.pid > 0
        # Wait for the worker → fake-CLI chain to finish writing
        _reap(result, timeout_s=5.0)
        log_body = _wait_for_log(result.log_path, contains="fake-aider-output")
        assert "fake-aider-output" in log_body
        # Cancel watchdog so the timer thread doesn't outlive the test
        AiderAdapter.cancel_timeout(result)

    def test_argv_assembly_includes_required_flags(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-x")
        workdir = _make_workdir(tmp_path)
        adapter = AiderAdapter()

        result = adapter.spawn(
            prompt="argv-shape-check",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        _wait_for_dump(fake_cli_fixture.argv_dump)
        AiderAdapter.cancel_timeout(result)

        argv = fake_cli_fixture.read_argv()
        # argv[0] is the wrapper path (resolved from PATH), so we check
        # the rest of the argv shape.
        assert "--model" in argv
        assert "--message" in argv
        assert "--yes" in argv
        assert argv[argv.index("--message") + 1] == "argv-shape-check"

    def test_env_strips_unrelated_secrets(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(
            monkeypatch,
            tmp_path,
            ANTHROPIC_API_KEY="ant-aider-isolated",
            DATABASE_URL="postgres://master",
            AWS_SECRET_ACCESS_KEY="aws-master",
        )
        workdir = _make_workdir(tmp_path)
        adapter = AiderAdapter()

        result = adapter.spawn(
            prompt="env-isolation-check",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-e2e-3",
        )
        _reap(result, timeout_s=5.0)
        _wait_for_dump(fake_cli_fixture.env_dump)
        AiderAdapter.cancel_timeout(result)

        env = fake_cli_fixture.read_env()
        assert env.get("ANTHROPIC_API_KEY") == "ant-aider-isolated"
        assert "DATABASE_URL" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env

    def test_nonzero_exit_propagates_to_log(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-x")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(
            mode="error",
            exit_code=7,
            stderr="aider exploded\n",
        )
        adapter = AiderAdapter()

        # Aider doesn't probe fast-exit, so spawn returns immediately.
        result = adapter.spawn(
            prompt="this-will-fail",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-e2e-4",
        )
        exit_code = _reap(result, timeout_s=5.0)
        AiderAdapter.cancel_timeout(result)
        assert exit_code == 7, f"expected exit 7, got {exit_code}"

        log_body = _wait_for_log(result.log_path, contains="aider exploded")
        assert "aider exploded" in log_body

    def test_partial_output_then_die_truncates_log(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Aider that streams a few lines then dies leaves a partial log.

        Verifies the adapter does NOT block waiting for clean shutdown
        before letting callers reap — the worker propagates the upstream
        non-zero exit and the log contains the partial output that DID
        make it through before death.
        """
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-x")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(mode="stream_then_die", exit_code=9)
        adapter = AiderAdapter()

        result = adapter.spawn(
            prompt="streaming-die",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-e2e-5",
        )
        exit_code = _reap(result, timeout_s=5.0)
        AiderAdapter.cancel_timeout(result)
        assert exit_code == 9
        log_body = _wait_for_log(result.log_path, contains="partial-output-line")
        # Truncated stream MUST be present — captured output isn't lost
        # just because the process died early.
        assert "partial-output-line" in log_body


# ---------------------------------------------------------------------------
# Codex adapter (probes fast-exit)
# ---------------------------------------------------------------------------


class TestCodexEndToEnd:
    """CodexAdapter spawn → fake-CLI execution."""

    def test_spawn_with_no_api_key_warns_but_succeeds(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Adapter logs a warning when OPENAI_API_KEY is absent; spawn
        # still proceeds (the upstream CLI itself may then error out).
        _isolated_env(monkeypatch, tmp_path)
        workdir = _make_workdir(tmp_path)
        adapter = CodexAdapter()

        result = adapter.spawn(
            prompt="codex-success",
            workdir=workdir,
            model_config=_model_for("codex"),
            session_id="codex-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        CodexAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-codex-output")
        assert "fake-codex-output" in log_body
        # Codex argv contains the prompt as a positional after flags
        argv = fake_cli_fixture.read_argv()
        assert "exec" in argv
        assert "--full-auto" in argv
        assert "--json" in argv

    def test_fast_exit_with_nonzero_raises_spawn_error(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, OPENAI_API_KEY="sk-test")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(
            mode="error",
            exit_code=3,
            stderr="codex configuration invalid\n",
        )
        adapter = CodexAdapter()

        with pytest.raises(SpawnError, match="exited early"):
            adapter.spawn(
                prompt="codex-fails-fast",
                workdir=workdir,
                model_config=_model_for("codex"),
                session_id="codex-e2e-2",
            )

    def test_env_excludes_master_secret_bytes(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(
            monkeypatch,
            tmp_path,
            OPENAI_API_KEY="sk-codex-isolated",
            ANTHROPIC_API_KEY="ant-master-not-for-codex",
            STRIPE_SECRET="sk-stripe-master",
        )
        workdir = _make_workdir(tmp_path)
        adapter = CodexAdapter()

        result = adapter.spawn(
            prompt="codex-env-check",
            workdir=workdir,
            model_config=_model_for("codex"),
            session_id="codex-e2e-3",
        )
        _reap(result, timeout_s=5.0)
        _wait_for_dump(fake_cli_fixture.env_dump)
        CodexAdapter.cancel_timeout(result)

        env = fake_cli_fixture.read_env()
        assert env.get("OPENAI_API_KEY") == "sk-codex-isolated"
        # Anthropic and Stripe master keys must NOT cross over
        assert "ANTHROPIC_API_KEY" not in env
        assert "STRIPE_SECRET" not in env


# ---------------------------------------------------------------------------
# Gemini adapter
# ---------------------------------------------------------------------------


class TestGeminiEndToEnd:
    """GeminiAdapter spawn → fake-CLI execution."""

    def test_spawn_succeeds_with_oauth_fallback(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Gemini supports OAuth so spawn proceeds even without API keys.
        _isolated_env(monkeypatch, tmp_path)
        workdir = _make_workdir(tmp_path)
        adapter = GeminiAdapter()

        result = adapter.spawn(
            prompt="gemini-succeeds",
            workdir=workdir,
            model_config=_model_for("gemini"),
            session_id="gemini-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        GeminiAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-gemini-output")
        assert "fake-gemini-output" in log_body

    def test_argv_carries_prompt_and_yolo(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, GOOGLE_API_KEY="AIzaXX")
        workdir = _make_workdir(tmp_path)
        adapter = GeminiAdapter()

        result = adapter.spawn(
            prompt="gemini-prompt-text",
            workdir=workdir,
            model_config=_model_for("gemini"),
            session_id="gemini-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        _wait_for_dump(fake_cli_fixture.argv_dump)
        GeminiAdapter.cancel_timeout(result)

        argv = fake_cli_fixture.read_argv()
        assert "-p" in argv
        assert argv[argv.index("-p") + 1] == "gemini-prompt-text"
        assert "--yolo" in argv

    def test_fast_exit_nonzero_raises(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, GOOGLE_API_KEY="AIzaY")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(mode="error", exit_code=4)
        adapter = GeminiAdapter()

        with pytest.raises(SpawnError, match="exited early"):
            adapter.spawn(
                prompt="gemini-fails",
                workdir=workdir,
                model_config=_model_for("gemini"),
                session_id="gemini-e2e-3",
            )


# ---------------------------------------------------------------------------
# Ollama adapter (Aider-via-Ollama)
# ---------------------------------------------------------------------------


class TestOllamaEndToEnd:
    """OllamaAdapter spawn → fake-CLI (aider profile reused)."""

    def test_spawn_succeeds(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Override profile mapping: Ollama adapter calls "aider" but the
        # fake's profile is also "aider"; output is correct as-is.
        _isolated_env(monkeypatch, tmp_path)
        workdir = _make_workdir(tmp_path)
        adapter = OllamaAdapter()

        result = adapter.spawn(
            prompt="ollama-task",
            workdir=workdir,
            model_config=_model_for("ollama"),
            session_id="ollama-e2e-1",
        )
        _reap(result, timeout_s=5.0)
        OllamaAdapter.cancel_timeout(result)

        log_body = _wait_for_log(result.log_path, contains="fake-aider-output")
        assert "fake-aider-output" in log_body

    def test_env_carries_ollama_base_url(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path)
        workdir = _make_workdir(tmp_path)
        adapter = OllamaAdapter(base_url="http://127.0.0.1:11434")

        result = adapter.spawn(
            prompt="ollama-env-check",
            workdir=workdir,
            model_config=_model_for("ollama"),
            session_id="ollama-e2e-2",
        )
        _reap(result, timeout_s=5.0)
        _wait_for_dump(fake_cli_fixture.env_dump)
        OllamaAdapter.cancel_timeout(result)

        env = fake_cli_fixture.read_env()
        assert env.get("OLLAMA_API_BASE") == "http://127.0.0.1:11434"
        assert env.get("OLLAMA_HOST") == "http://127.0.0.1:11434"

    def test_env_strips_cloud_master_keys(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Ollama is meant to be air-gapped — cloud master keys must NOT
        # leak through even when the operator has them set.
        _isolated_env(
            monkeypatch,
            tmp_path,
            ANTHROPIC_API_KEY="ant-leak-test",
            OPENAI_API_KEY="sk-leak-test",
        )
        workdir = _make_workdir(tmp_path)
        adapter = OllamaAdapter()

        result = adapter.spawn(
            prompt="ollama-no-cloud",
            workdir=workdir,
            model_config=_model_for("ollama"),
            session_id="ollama-e2e-3",
        )
        _reap(result, timeout_s=5.0)
        _wait_for_dump(fake_cli_fixture.env_dump)
        OllamaAdapter.cancel_timeout(result)

        env = fake_cli_fixture.read_env()
        # Master cloud keys must NOT cross into a local-only adapter
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env


# ---------------------------------------------------------------------------
# Claude adapter (most complex — wraps stream-json through pipe)
# ---------------------------------------------------------------------------


class TestClaudeEndToEnd:
    """ClaudeCodeAdapter spawn → fake-CLI stream-json → wrapper log."""

    def _spawn(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        session_id: str,
        *,
        prompt: str = "claude-test",
    ) -> tuple[ClaudeCodeAdapter, Path]:
        workdir = _make_workdir(tmp_path)
        adapter = ClaudeCodeAdapter()
        result = adapter.spawn(
            prompt=prompt,
            workdir=workdir,
            model_config=_model_for("claude"),
            session_id=f"qa-{session_id}",  # role is parsed from session prefix
        )
        # Wait for both the claude_proc and the wrapper_proc (which
        # consumes its stdout) to drain.
        _reap(result, timeout_s=8.0)
        adapter.cancel_timeout(result)
        return adapter, result.log_path

    def test_stream_json_decoded_by_wrapper(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-claude-x")
        _adapter, log_path = self._spawn(
            tmp_path,
            fake_cli_fixture,
            "stream-1",
            prompt="claude-says-hi",
        )

        # Wrapper transforms stream-json events into human-readable lines.
        log_body = _wait_for_log(log_path, contains="fake-claude")
        # Either the assistant text or the result text should appear
        assert "fake-claude-stream-ok" in log_body or "fake-claude-result" in log_body

    def test_argv_carries_required_flags(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-claude-x")
        self._spawn(tmp_path, fake_cli_fixture, "argv-1")

        _wait_for_dump(fake_cli_fixture.argv_dump)
        argv = fake_cli_fixture.read_argv()
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--permission-mode" in argv
        assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
        # Prompt is delivered via -p
        assert "-p" in argv

    def test_env_isolates_secrets(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(
            monkeypatch,
            tmp_path,
            ANTHROPIC_API_KEY="ant-claude-isolated",
            OPENAI_API_KEY="sk-not-for-claude",
            DATABASE_URL="postgres://master",
        )
        self._spawn(tmp_path, fake_cli_fixture, "env-1")

        _wait_for_dump(fake_cli_fixture.env_dump)
        env = fake_cli_fixture.read_env()
        assert env.get("ANTHROPIC_API_KEY") == "ant-claude-isolated"
        # OpenAI and DB master keys must NOT cross over
        assert "OPENAI_API_KEY" not in env
        assert "DATABASE_URL" not in env

    def test_fast_exit_nonzero_raises(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-x")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(
            mode="error",
            exit_code=5,
            stderr="claude: fatal init error\n",
        )
        adapter = ClaudeCodeAdapter()

        with pytest.raises(SpawnError, match="exited early"):
            adapter.spawn(
                prompt="claude-fails",
                workdir=workdir,
                model_config=_model_for("claude"),
                session_id="qa-fast-fail-1",
            )

    def test_rate_limit_detected_in_tail(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Stderr containing "rate limit" must surface as RateLimitError.
        from bernstein.adapters.base import RateLimitError

        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-x")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(
            mode="error",
            exit_code=1,
            stdout="rate limit hit — try again later\n",
            stderr="rate limit exceeded\n",
        )
        adapter = ClaudeCodeAdapter()

        with pytest.raises(RateLimitError):
            adapter.spawn(
                prompt="claude-rate-limited",
                workdir=workdir,
                model_config=_model_for("claude"),
                session_id="qa-ratelimit-1",
            )


# ---------------------------------------------------------------------------
# Regression tests for bugs uncovered by the harness
# ---------------------------------------------------------------------------


class TestAdapterTimeoutHandling:
    """Watchdog timer fires when the upstream CLI hangs."""

    def test_aider_timeout_kills_hung_process(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hung CLI must be killed by the adapter's timeout watchdog.

        Configures the fake to ``hang`` (sleep forever), spawns with a
        2-second timeout, and asserts the process exits within the
        SIGTERM grace window (default 30s — we use 5s for the assertion
        because pytest tests should not hang for half a minute).
        """
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-x")
        workdir = _make_workdir(tmp_path)
        fake_cli_fixture.configure(mode="hang")
        adapter = AiderAdapter()

        result = adapter.spawn(
            prompt="hang-please",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-e2e-hang-1",
            timeout_seconds=2,
        )
        # Watchdog SIGTERMs at t=2s, then SIGKILLs after a 30s grace
        # window; the fake_cli sleep(60) ignores SIGPIPE but not
        # SIGTERM, so the process should exit promptly on the SIGTERM.
        exit_code = _reap(result, timeout_s=10.0)
        AiderAdapter.cancel_timeout(result)
        # Negative exit code indicates death-by-signal on POSIX
        assert exit_code != 0, "watchdog should have killed the hung process"


class TestAdapterBugRegressions:
    """Regression tests for adapter bugs the fake-CLI harness exposed."""

    def test_aider_stores_proc_handle_for_zombie_safe_is_alive(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AiderAdapter.spawn() must thread the Popen handle into SpawnResult.

        Without it, ``is_alive()`` falls back to ``process_alive(pid)``
        which returns True for zombie processes that haven't been
        reaped, causing the orchestrator to wait indefinitely on a
        finished agent.  Bug discovered when the integration harness
        timed out polling ``os.kill(pid, 0)`` on a worker that had
        already exited.
        """
        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="x")
        workdir = _make_workdir(tmp_path)
        adapter = AiderAdapter()

        result = adapter.spawn(
            prompt="zombie-test",
            workdir=workdir,
            model_config=_model_for("aider"),
            session_id="aider-zombie-1",
        )
        # The fix wires proc through SpawnResult; assert it's there.
        assert result.proc is not None, "AiderAdapter must set SpawnResult.proc"
        assert hasattr(result.proc, "wait")
        # Drain the worker so it doesn't leak
        result.proc.wait(timeout=5.0)
        AiderAdapter.cancel_timeout(result)

    def test_ollama_stores_proc_handle(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same regression for OllamaAdapter — both adapters share the
        ``aider`` upstream and originally diverged from the codex/gemini/
        claude adapters in dropping the ``proc`` handle.
        """
        _isolated_env(monkeypatch, tmp_path)
        workdir = _make_workdir(tmp_path)
        adapter = OllamaAdapter()

        result = adapter.spawn(
            prompt="zombie-test-ollama",
            workdir=workdir,
            model_config=_model_for("ollama"),
            session_id="ollama-zombie-1",
        )
        assert result.proc is not None, "OllamaAdapter must set SpawnResult.proc"
        result.proc.wait(timeout=5.0)
        OllamaAdapter.cancel_timeout(result)

    def test_claude_rate_limit_signal_in_stderr_is_detected(
        self,
        tmp_path: Path,
        fake_cli_fixture: FakeCLIHandle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Rate-limit banners on stderr must surface as RateLimitError.

        Before the fix, ``CLIAdapter._read_last_lines`` only consulted
        ``log_path``, but the Claude Code adapter pipes upstream stdout
        through a wrapper that drops non-JSON.  Real-world rate-limit
        messages ("you've hit your limit, resets at...") arrive as
        non-JSON stderr text and were silently swallowed — the probe
        would raise a generic SpawnError instead of RateLimitError, so
        the orchestrator's rate-limit cooldown never engaged.

        With the fix, ``_read_last_lines`` also reads the
        ``.stderr.log`` sibling, so the heuristic catches the banner.
        """
        from bernstein.adapters.base import RateLimitError

        _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="x")
        workdir = _make_workdir(tmp_path)
        # Stdout will contain valid JSON (so the wrapper consumes it),
        # but stderr will contain ONLY the rate-limit banner — exactly
        # the shape that previously slipped past detection.
        fake_cli_fixture.configure(
            mode="error",
            exit_code=1,
            stdout=json.dumps({"type": "system", "subtype": "init"}),
            stderr="you've hit your limit; resets in 4h",
        )
        adapter = ClaudeCodeAdapter()

        with pytest.raises(RateLimitError):
            adapter.spawn(
                prompt="trigger",
                workdir=workdir,
                model_config=_model_for("claude"),
                session_id="qa-stderr-rl-1",
            )


# ---------------------------------------------------------------------------
# Cross-adapter env isolation regression — confirms the harness itself works
# ---------------------------------------------------------------------------


class TestHarnessSelfCheck:
    """Smoke-test the fake-CLI harness independently of any adapter."""

    def test_wrapper_resolves_to_fake_cli_via_path(
        self,
        fake_cli_fixture: FakeCLIHandle,
    ) -> None:
        # Confirm that `claude` on PATH points to our fake.
        result = subprocess.run(
            [
                "claude",
                "--print",
                "--permission-mode",
                "bypassPermissions",
                "--output-format",
                "stream-json",
                "-p",
                "smoke",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0
        # First NDJSON line is the system init event
        first = result.stdout.splitlines()[0]
        payload = json.loads(first)
        assert payload["type"] == "system"

    def test_configure_switches_mode(
        self,
        fake_cli_fixture: FakeCLIHandle,
    ) -> None:
        fake_cli_fixture.configure(mode="error", exit_code=11)
        result = subprocess.run(
            ["aider", "--model", "x", "--message", "y", "--yes"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 11
