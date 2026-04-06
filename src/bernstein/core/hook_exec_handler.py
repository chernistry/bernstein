"""HOOK-005: Exec handler type for hook events.

Runs a shell command when a hook event fires.  Payload fields are
serialised as ``BERNSTEIN_HOOK_*`` environment variables and the full
payload is written to stdin as JSON.  stdout and stderr are captured.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bernstein.core.hook_events import HookEvent, HookPayload

logger = logging.getLogger(__name__)

# Default timeout for exec handlers (seconds).
EXEC_HANDLER_TIMEOUT_S: float = 30.0


@dataclass(frozen=True)
class ExecResult:
    """Result from running an exec handler.

    Attributes:
        returncode: Process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.
        timed_out: Whether the process was killed due to timeout.
    """

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _build_env_vars(event: HookEvent, payload: HookPayload) -> dict[str, str]:
    """Build environment variables from a hook payload.

    All keys are uppercased and prefixed with ``BERNSTEIN_HOOK_``.
    Only string-coercible scalar values are included.

    Args:
        event: The hook event.
        payload: The payload to serialise.

    Returns:
        Dict of environment variable name to value.
    """
    env: dict[str, str] = {
        "BERNSTEIN_HOOK_EVENT": event.value,
        "BERNSTEIN_HOOK_TIMESTAMP": str(payload.timestamp),
    }
    payload_dict = payload.to_dict()
    for key, value in payload_dict.items():
        if key in ("event", "timestamp", "metadata"):
            continue
        env_key = f"BERNSTEIN_HOOK_{key.upper()}"
        if isinstance(value, (str, int, float, bool)):
            env[env_key] = str(value)
    return env


async def run_exec_handler(
    command: str,
    event: HookEvent,
    payload: HookPayload,
    *,
    timeout_s: float = EXEC_HANDLER_TIMEOUT_S,
    extra_env: dict[str, str] | None = None,
) -> ExecResult:
    """Execute a shell command as a hook handler.

    The command is run via ``/bin/sh -c`` with:
    - ``BERNSTEIN_HOOK_*`` env vars set from the payload
    - Full payload JSON written to stdin

    Args:
        command: Shell command to run.
        event: The triggering hook event.
        payload: The hook payload.
        timeout_s: Maximum seconds to wait for the command.
        extra_env: Additional environment variables to set.

    Returns:
        Execution result with captured output.
    """
    import os

    env = dict(os.environ)
    env.update(_build_env_vars(event, payload))
    if extra_env:
        env.update(extra_env)

    payload_json = json.dumps(payload.to_dict())

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=payload_json.encode()),
                timeout=timeout_s,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                "Exec handler timed out after %.1fs for event %s: %s",
                timeout_s,
                event.value,
                command,
            )
            return ExecResult(
                returncode=-1,
                stdout="",
                stderr=f"Timed out after {timeout_s}s",
                timed_out=True,
            )

        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        returncode = proc.returncode or 0

        if returncode != 0:
            logger.warning(
                "Exec handler exited %d for event %s: %s (stderr: %s)",
                returncode,
                event.value,
                command,
                stderr[:200],
            )

        return ExecResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    except OSError as exc:
        logger.error(
            "Failed to start exec handler for event %s: %s — %s",
            event.value,
            command,
            exc,
        )
        return ExecResult(
            returncode=-1,
            stdout="",
            stderr=str(exc),
        )


def make_exec_hook_handler(
    command: str,
    timeout_s: float = EXEC_HANDLER_TIMEOUT_S,
    extra_env: dict[str, str] | None = None,
) -> ExecHookHandler:
    """Create an async handler function wrapping an exec command.

    The returned handler conforms to the ``AsyncHookHandler`` protocol
    and can be registered in the ``AsyncHookRegistry``.

    Args:
        command: Shell command to run.
        timeout_s: Timeout in seconds.
        extra_env: Extra environment variables.

    Returns:
        An async callable suitable for hook registration.
    """
    return ExecHookHandler(command=command, timeout_s=timeout_s, extra_env=extra_env or {})


class ExecHookHandler:
    """Async-callable wrapper that runs a shell command on hook events.

    Attributes:
        command: The shell command to run.
        timeout_s: Timeout in seconds.
        extra_env: Extra environment variables.
        last_result: The result of the most recent execution.
    """

    def __init__(
        self,
        command: str,
        timeout_s: float = EXEC_HANDLER_TIMEOUT_S,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.timeout_s = timeout_s
        self.extra_env = extra_env or {}
        self.last_result: ExecResult | None = None

    async def __call__(self, event: HookEvent, payload: HookPayload) -> None:
        """Execute the shell command for the given event.

        Args:
            event: The hook event.
            payload: The hook payload.

        Raises:
            RuntimeError: If the command exits with a non-zero code.
        """
        result = await run_exec_handler(
            self.command,
            event,
            payload,
            timeout_s=self.timeout_s,
            extra_env=self.extra_env,
        )
        self.last_result = result
        if result.timed_out:
            msg = f"Exec handler timed out: {self.command}"
            raise RuntimeError(msg)
        if result.returncode != 0:
            msg = f"Exec handler exited {result.returncode}: {result.stderr[:200]}"
            raise RuntimeError(msg)
