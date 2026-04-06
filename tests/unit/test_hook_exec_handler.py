"""Tests for HOOK-005 — exec handler type."""

from __future__ import annotations

import json
import sys

import pytest

from bernstein.core.hook_events import HookEvent, HookPayload, TaskPayload
from bernstein.core.hook_exec_handler import (
    ExecHookHandler,
    ExecResult,
    _build_env_vars,
    make_exec_hook_handler,
    run_exec_handler,
)


# ---------------------------------------------------------------------------
# Environment variable building
# ---------------------------------------------------------------------------


class TestBuildEnvVars:
    """_build_env_vars produces correct BERNSTEIN_HOOK_ variables."""

    def test_includes_event_name(self) -> None:
        payload = HookPayload(event=HookEvent.TASK_COMPLETED)
        env = _build_env_vars(HookEvent.TASK_COMPLETED, payload)
        assert env["BERNSTEIN_HOOK_EVENT"] == "task.completed"

    def test_includes_timestamp(self) -> None:
        payload = HookPayload(event=HookEvent.TASK_COMPLETED, timestamp=1234567890.0)
        env = _build_env_vars(HookEvent.TASK_COMPLETED, payload)
        assert env["BERNSTEIN_HOOK_TIMESTAMP"] == "1234567890.0"

    def test_includes_task_fields(self) -> None:
        payload = TaskPayload(
            event=HookEvent.TASK_FAILED,
            task_id="t-42",
            role="backend",
            title="Fix bug",
            error="timeout",
        )
        env = _build_env_vars(HookEvent.TASK_FAILED, payload)
        assert env["BERNSTEIN_HOOK_TASK_ID"] == "t-42"
        assert env["BERNSTEIN_HOOK_ROLE"] == "backend"
        assert env["BERNSTEIN_HOOK_TITLE"] == "Fix bug"
        assert env["BERNSTEIN_HOOK_ERROR"] == "timeout"

    def test_excludes_metadata_key(self) -> None:
        payload = HookPayload(
            event=HookEvent.ORCHESTRATOR_TICK,
            metadata={"key": "val"},
        )
        env = _build_env_vars(HookEvent.ORCHESTRATOR_TICK, payload)
        assert "BERNSTEIN_HOOK_METADATA" not in env


# ---------------------------------------------------------------------------
# Exec handler execution
# ---------------------------------------------------------------------------


class TestRunExecHandler:
    """run_exec_handler runs shell commands with payload."""

    @pytest.mark.asyncio
    async def test_successful_command(self) -> None:
        result = await run_exec_handler(
            "echo hello",
            HookEvent.TASK_COMPLETED,
            HookPayload(event=HookEvent.TASK_COMPLETED),
        )
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_failed_command(self) -> None:
        result = await run_exec_handler(
            "exit 1",
            HookEvent.TASK_FAILED,
            HookPayload(event=HookEvent.TASK_FAILED),
        )
        assert result.returncode == 1
        assert not result.timed_out

    @pytest.mark.asyncio
    async def test_stderr_captured(self) -> None:
        result = await run_exec_handler(
            "echo error >&2",
            HookEvent.TASK_FAILED,
            HookPayload(event=HookEvent.TASK_FAILED),
        )
        assert "error" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self) -> None:
        result = await run_exec_handler(
            "sleep 60",
            HookEvent.TASK_COMPLETED,
            HookPayload(event=HookEvent.TASK_COMPLETED),
            timeout_s=0.1,
        )
        assert result.timed_out
        assert result.returncode == -1

    @pytest.mark.asyncio
    async def test_payload_json_on_stdin(self) -> None:
        payload = TaskPayload(
            event=HookEvent.TASK_COMPLETED,
            task_id="t-1",
            role="qa",
            title="Run tests",
        )
        # Read stdin and echo it back
        result = await run_exec_handler(
            f'{sys.executable} -c "import sys; print(sys.stdin.read())"',
            HookEvent.TASK_COMPLETED,
            payload,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout.strip())
        assert parsed["task_id"] == "t-1"
        assert parsed["event"] == "task.completed"

    @pytest.mark.asyncio
    async def test_env_vars_available_to_command(self) -> None:
        payload = TaskPayload(
            event=HookEvent.TASK_COMPLETED,
            task_id="t-99",
            role="backend",
            title="Deploy",
        )
        result = await run_exec_handler(
            "echo $BERNSTEIN_HOOK_TASK_ID",
            HookEvent.TASK_COMPLETED,
            payload,
        )
        assert "t-99" in result.stdout

    @pytest.mark.asyncio
    async def test_extra_env_passed_through(self) -> None:
        result = await run_exec_handler(
            "echo $MY_CUSTOM_VAR",
            HookEvent.TASK_COMPLETED,
            HookPayload(event=HookEvent.TASK_COMPLETED),
            extra_env={"MY_CUSTOM_VAR": "custom_value"},
        )
        assert "custom_value" in result.stdout


# ---------------------------------------------------------------------------
# ExecHookHandler class
# ---------------------------------------------------------------------------


class TestExecHookHandler:
    """ExecHookHandler wraps exec handler as async callable."""

    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        handler = ExecHookHandler(command="echo ok")
        await handler(HookEvent.TASK_COMPLETED, HookPayload(event=HookEvent.TASK_COMPLETED))
        assert handler.last_result is not None
        assert handler.last_result.returncode == 0

    @pytest.mark.asyncio
    async def test_failure_raises(self) -> None:
        handler = ExecHookHandler(command="exit 1")
        with pytest.raises(RuntimeError, match="exited 1"):
            await handler(HookEvent.TASK_COMPLETED, HookPayload(event=HookEvent.TASK_COMPLETED))

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        handler = ExecHookHandler(command="sleep 60", timeout_s=0.1)
        with pytest.raises(RuntimeError, match="timed out"):
            await handler(HookEvent.TASK_COMPLETED, HookPayload(event=HookEvent.TASK_COMPLETED))

    @pytest.mark.asyncio
    async def test_make_exec_hook_handler(self) -> None:
        handler = make_exec_hook_handler("echo test", timeout_s=5.0)
        assert isinstance(handler, ExecHookHandler)
        assert handler.command == "echo test"
        assert handler.timeout_s == 5.0
