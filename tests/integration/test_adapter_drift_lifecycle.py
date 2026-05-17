"""Integration tests for adapter contract drift during a run.

These tests treat the adapter subprocess as an *unreliable peer* and
assert that the spawn lifecycle does not crash, leak, or produce a
zombie when the upstream CLI misbehaves at runtime.

The :mod:`tests.integration.fake_cli` harness already supports the
fault-injection modes we need (``error``, ``stream_then_die``,
``no_output``, ``hang``); these tests wire them together with the
production adapter spawn path to assert the integration surface.

Failure modes covered:

| Mode                                       | Test |
|--------------------------------------------|------|
| CLI exits 42 unexpectedly mid-run          | ``test_unexpected_exit_code_42_does_not_crash_caller`` |
| CLI emits malformed JSON then dies         | ``test_malformed_stream_then_die_logs_and_exits`` |
| CLI produces no output, exit 0             | ``test_no_output_success_does_not_orphan`` |
| Manager-injected env reaches the CLI       | ``test_env_isolation_strips_master_keys`` |
| Adapter caller never raises on non-zero rc | ``test_caller_sees_nonzero_rc_without_exception`` |
| Two back-to-back drift spawns in same test | ``test_repeated_drift_spawns_dont_leak_handles`` |
| Empty stdout body still produces log file  | ``test_no_output_mode_leaves_log_file_present`` |

These complement ``test_adapter_e2e.py`` (which covers the happy-path
argv/env/exit-code matrix) by stress-testing the *unhappy* paths.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.aider import AiderAdapter

if TYPE_CHECKING:
    from bernstein.adapters.base import SpawnResult

    from .fake_cli.conftest_adapters import FakeCLIHandle

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        platform.system() == "Windows",
        reason="fake-CLI harness uses POSIX shell wrappers",
    ),
]


# ---------------------------------------------------------------------------
# Helpers (lifted from test_adapter_e2e.py — kept minimal and standalone)
# ---------------------------------------------------------------------------


def _make_workdir(tmp_path: Path) -> Path:
    """Initialise a minimal git workdir the Aider adapter expects."""
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


def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extras: str) -> None:
    """Strip env to the minimum the adapter spawn path needs."""
    keep = {"PATH", "TMPDIR", "HOME", "LANG", "LC_ALL", "USER", "SHELL", "TERM"}
    for k in list(os.environ.keys()):
        if k in keep or k.startswith("BERNSTEIN_FAKE_CLI_"):
            continue
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    for k, v in extras.items():
        monkeypatch.setenv(k, v)


def _reap(result: SpawnResult, *, timeout_s: float = 8.0) -> int:
    """Wait for a SpawnResult to exit; return the exit code or raise."""
    proc = getattr(result, "proc", None)
    pid = getattr(result, "pid", 0)
    if proc is not None and hasattr(proc, "wait"):
        try:
            return int(proc.wait(timeout=timeout_s))
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"worker pid {pid} did not exit within {timeout_s}s") from exc
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return 0
        time.sleep(0.025)
    raise TimeoutError(f"worker pid {pid} still alive after {timeout_s}s")


def _wait_for_log_nonempty(log_path: Path, *, timeout_s: float = 5.0) -> str:
    """Wait until *log_path* exists; return whatever body it has.

    Note: in *no_output* mode the log may be empty after the worker
    exits — callers should not assert on body content there.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if log_path.exists():
            return log_path.read_text(encoding="utf-8", errors="replace")
        time.sleep(0.025)
    raise TimeoutError(f"log {log_path} did not appear within {timeout_s}s")


def _aider_spawn(workdir: Path, *, session_id: str, prompt: str = "drift test") -> SpawnResult:
    """Standard spawn() invocation for the Aider adapter under fake CLI."""
    adapter = AiderAdapter()
    return adapter.spawn(
        prompt=prompt,
        workdir=workdir,
        model_config=ModelConfig(model="sonnet", effort="medium"),
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Unexpected exit code mid-run
# ---------------------------------------------------------------------------


def test_unexpected_exit_code_42_does_not_crash_caller(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI exits 42 from inside ``adapter.spawn`` — caller observes rc=42.

    Reproduces the failure mode "adapter exits with code 42
    unexpectedly". The spawn() call itself must succeed (it returns a
    SpawnResult); only the subsequent process wait surfaces the
    non-zero exit. The orchestrator handles non-zero exits by reaping.
    """
    _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test")
    fake_cli_fixture.configure(mode="error", exit_code=42, stderr="forty-two")
    workdir = _make_workdir(tmp_path)

    result = _aider_spawn(workdir, session_id="drift-rc42")
    assert result.pid > 0
    exit_code = _reap(result, timeout_s=5.0)
    AiderAdapter.cancel_timeout(result)

    assert exit_code == 42, f"expected rc=42 from fake CLI; got {exit_code}"
    # The stderr text must have been captured into the log alongside stdout.
    log_body = _wait_for_log_nonempty(result.log_path)
    assert "forty-two" in log_body, f"stderr missing from log: {log_body[:300]!r}"


def test_caller_sees_nonzero_rc_without_exception(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn() must not raise on a non-zero CLI exit — the worker wraps it.

    The adapter contract: ``spawn`` returns a :class:`SpawnResult`
    *regardless* of how the inner CLI ends up exiting. Non-zero exit
    surfaces only when the caller waits on the proc handle.
    """
    _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test")
    fake_cli_fixture.configure(mode="error", exit_code=99)
    workdir = _make_workdir(tmp_path)

    # Must not raise during spawn itself — the Aider adapter doesn't probe.
    result = _aider_spawn(workdir, session_id="drift-noraise")
    rc = _reap(result, timeout_s=5.0)
    AiderAdapter.cancel_timeout(result)
    assert rc == 99


# ---------------------------------------------------------------------------
# Stream-then-die / malformed output
# ---------------------------------------------------------------------------


def test_malformed_stream_then_die_logs_and_exits(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI emits partial output then dies — log shows both halves.

    The ``stream_then_die`` mode flushes one stdout line and one stderr
    line before exiting non-zero. Both must end up in the log, and the
    spawn lifecycle must complete cleanly (no zombie).
    """
    _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test")
    fake_cli_fixture.configure(mode="stream_then_die", exit_code=3)
    workdir = _make_workdir(tmp_path)

    result = _aider_spawn(workdir, session_id="drift-truncated")
    rc = _reap(result, timeout_s=5.0)
    AiderAdapter.cancel_timeout(result)

    assert rc == 3
    log_body = _wait_for_log_nonempty(result.log_path)
    assert "partial-output-line" in log_body, log_body[:300]
    assert "dying mid-stream" in log_body, log_body[:300]


def test_no_output_success_does_not_orphan(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI that exits 0 with no stdout/stderr is a valid (if empty) run.

    Guards against the regression where the adapter would wait
    indefinitely for "first output" before considering the spawn done.
    Without output the worker still exits 0 and is reaped cleanly.
    """
    _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test")
    fake_cli_fixture.configure(mode="no_output")
    workdir = _make_workdir(tmp_path)

    result = _aider_spawn(workdir, session_id="drift-silent")
    rc = _reap(result, timeout_s=5.0)
    AiderAdapter.cancel_timeout(result)

    assert rc == 0, f"silent CLI must exit 0 cleanly; got {rc}"

    # The log file is created up-front by the adapter — assert it's
    # present so log readers don't ENOENT mid-flight.
    assert result.log_path.exists(), "log file must be created even with no output"


def test_no_output_mode_leaves_log_file_present(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-output CLI: the log path is created and reapable (empty OK).

    Pins the contract that ``SpawnResult.log_path`` always exists, so
    the dashboard/streaming readers can ``open()`` it without
    branching on whether the run produced any bytes.
    """
    _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test")
    fake_cli_fixture.configure(mode="no_output")
    workdir = _make_workdir(tmp_path)

    result = _aider_spawn(workdir, session_id="drift-emptylog")
    _reap(result, timeout_s=5.0)
    AiderAdapter.cancel_timeout(result)

    assert result.log_path.exists()
    # ``stat`` succeeds even on an empty file.
    _ = result.log_path.stat()


# ---------------------------------------------------------------------------
# Env isolation contract
# ---------------------------------------------------------------------------


def test_env_isolation_strips_master_keys(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter spawn must not leak a master credential into the CLI env.

    Sets a master-secret-looking var on the orchestrator side, runs
    the spawn, then inspects the env dumped by the fake CLI. The
    master key must not appear, while the scoped ``ANTHROPIC_API_KEY``
    must.
    """
    _isolated_env(
        monkeypatch,
        tmp_path,
        ANTHROPIC_API_KEY="ant-test-scoped",
        BERNSTEIN_MASTER_OPENAI_KEY="sk-master-do-not-leak",
    )
    fake_cli_fixture.configure(mode="success")
    workdir = _make_workdir(tmp_path)

    result = _aider_spawn(workdir, session_id="drift-env")
    _reap(result, timeout_s=5.0)
    AiderAdapter.cancel_timeout(result)

    # Wait for the env dump file the fake_cli writes on every spawn.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if fake_cli_fixture.env_dump.exists() and fake_cli_fixture.env_dump.stat().st_size > 0:
            break
        time.sleep(0.025)

    env = fake_cli_fixture.read_env()
    assert env, "env dump should be non-empty"
    # The scoped key passes through (allowlist).
    assert env.get("ANTHROPIC_API_KEY") == "ant-test-scoped", env.get("ANTHROPIC_API_KEY")
    # The master key does NOT.
    assert "BERNSTEIN_MASTER_OPENAI_KEY" not in env, (
        f"master credential leaked into agent env: {env.get('BERNSTEIN_MASTER_OPENAI_KEY')!r}"
    )


# ---------------------------------------------------------------------------
# Resource cleanup under repeated drift
# ---------------------------------------------------------------------------


def test_repeated_drift_spawns_dont_leak_handles(
    tmp_path: Path,
    fake_cli_fixture: FakeCLIHandle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three back-to-back error-mode spawns: every one is reaped cleanly.

    Models a worst-case lifecycle where the adapter keeps crashing.
    The orchestrator-side accumulation pattern must not leak the
    underlying log file handles or wedge the wrapper Popen objects.
    """
    _isolated_env(monkeypatch, tmp_path, ANTHROPIC_API_KEY="ant-test")
    fake_cli_fixture.configure(mode="error", exit_code=7)
    workdir = _make_workdir(tmp_path)

    rcs: list[int] = []
    log_paths: list[Path] = []
    for i in range(3):
        result = _aider_spawn(workdir, session_id=f"drift-loop-{i:02d}")
        rcs.append(_reap(result, timeout_s=5.0))
        AiderAdapter.cancel_timeout(result)
        log_paths.append(result.log_path)

    assert rcs == [7, 7, 7], rcs
    # Every spawn produced its own (distinct) log file.
    assert len({p.resolve() for p in log_paths}) == 3, [str(p) for p in log_paths]
    for p in log_paths:
        assert p.exists()


# ---------------------------------------------------------------------------
# Worker entry-point with non-existent inner CLI (drift before any I/O)
# ---------------------------------------------------------------------------


def test_worker_with_nonexistent_inner_binary_exits_127(tmp_path: Path) -> None:
    """The ``bernstein-worker`` wrapper exits 127 for a missing inner binary.

    Mirrors the contract from ``test_worker_subprocess_signals.py`` but
    asserts it from the *manager's* perspective: this is what the
    orchestrator/reaper will observe when the adapter binary itself is
    missing on PATH — distinct from "CLI ran but errored".
    """
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bernstein.core.orchestration.worker",
            "--role",
            "test",
            "--session",
            "drift-no-binary",
            "--pid-dir",
            str(pid_dir),
            "--",
            "/usr/bin/this-binary-does-not-exist-bernstein",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rc = proc.wait(timeout=10)
    assert rc == 127, f"missing inner binary should yield rc=127; got {rc}"
    # And no PID file is left behind.
    assert list(pid_dir.iterdir()) == []
