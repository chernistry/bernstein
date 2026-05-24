"""Devin for Terminal (Cognition) CLI adapter.

`Devin for Terminal` is Cognition's local coding agent - a single-binary
``devin`` CLI installed via::

    curl -fsSL https://cli.devin.ai/install.sh | bash

Headless usage matches the Codex shape: the CLI prints the assistant's
reply for one prompt and exits when invoked with ``-p`` / ``--print``,
which is the documented non-interactive mode (see
``https://cli.devin.ai/docs/reference/commands``).  Authentication is
handled out of band by ``devin auth login``; for unattended runs the
CLI also reads ``DEVIN_API_KEY`` (and optional ``DEVIN_ORG_ID``)
matching Cognition's REST API tokens (``apk_…`` / ``cog_…`` prefixes).
``WINDSURF_API_KEY`` is honoured for the Windsurf-bundled distribution.

Last verified against Devin for Terminal docs on 2026-05-05.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING, Any

from bernstein.adapters.base import (
    DEFAULT_TIMEOUT_SECONDS,
    CLIAdapter,
    SpawnResult,
    build_worker_cmd,
)
from bernstein.adapters.env_isolation import build_filtered_env

if TYPE_CHECKING:
    from pathlib import Path

    from bernstein.core.models import ModelConfig

logger = logging.getLogger(__name__)

# Headless CLI flags. Cognition documents ``-p``/``--print`` as the
# non-interactive mode that prints the assistant reply and exits, with
# the prompt accepted either inline (``devin -p "..."``) or after a
# ``--`` separator. We use the inline form for parity with codex/aider.
_NON_INTERACTIVE_FLAG = "--print"

# ``--permission-mode bypass`` mirrors Codex's ``--full-auto`` -
# required for unattended runs so Devin does not stop to ask for
# tool-execution confirmations.
_PERMISSION_MODE_FLAG = "--permission-mode"
_PERMISSION_MODE_BYPASS = "bypass"


class DevinTerminalAdapter(CLIAdapter):
    """Spawn and monitor Devin for Terminal (Cognition) sessions.

    Devin for Terminal is the local-binary sibling of Cognition's cloud
    Devin: it executes the same plan/code/verify loop on the user's
    machine and can hand off to a cloud session via ``devin push``.
    Bernstein only drives the local agent; cloud handoff is a non-goal.

    The adapter intentionally raises *only* at :meth:`spawn` time -
    importing this module never touches the env, so missing credentials
    surface as a runtime warning when an actual task is dispatched
    (matches the ``CLM`` adapter behaviour).
    """

    # api.devin.ai serves the REST API; cli.devin.ai serves install
    # artefacts and is contacted only at install time, but listing it
    # here keeps the network-policy check honest if a future binary
    # phones home for self-update.
    external_endpoints = (
        ("api.devin.ai", 443),
        ("cli.devin.ai", 443),
    )

    def spawn(
        self,
        *,
        prompt: str,
        workdir: Path,
        model_config: ModelConfig,
        session_id: str,
        mcp_config: dict[str, Any] | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        task_scope: str = "medium",
        budget_multiplier: float = 1.0,
        system_addendum: str = "",
        multimodal_context: Any | None = None,
    ) -> SpawnResult:
        """Launch a one-shot Devin for Terminal session.

        Args:
            prompt: Task prompt - passed inline to ``devin --print``.
            workdir: Working directory; Devin treats this as the
                project root.
            model_config: Bernstein model selection. ``model`` is
                forwarded via ``--model``; if blank the CLI falls back
                to its configured default.
            session_id: Unique session identifier used for log naming
                and the bernstein-worker title.
            mcp_config: Optional MCP server definitions (unused -
                Devin manages MCP via its own ``devin mcp`` subcommand
                and config file).
            timeout_seconds: Process wall-clock timeout.
            task_scope: Task scope hint (unused by Devin).
            budget_multiplier: Retry budget multiplier (unused).
            system_addendum: Protocol-critical instructions; Devin's
                CLI takes a single ``--print`` argument so the
                addendum is appended to the user prompt.

        Returns:
            SpawnResult describing the spawned process.

        Raises:
            RuntimeError: The ``devin`` binary is missing from PATH or
                the OS denies execution.
        """
        self.refuse_multimodal_if_needed(multimodal_context)
        self.enforce_network_policy()
        log_path = workdir / ".sdd" / "runtime" / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # No first-class system-prompt channel - graft any addendum
        # onto the prompt so completion / heartbeat instructions still
        # reach the agent. Empty addenda are no-ops.
        full_prompt = f"{prompt}\n\n{system_addendum}".rstrip() if system_addendum else prompt

        # Surface the missing-credentials case as a warning rather than
        # a hard error: ``devin auth login`` is the recommended setup
        # path and writes a credential cache that the CLI prefers over
        # env vars, so a fresh user could legitimately have neither
        # ``DEVIN_API_KEY`` nor ``WINDSURF_API_KEY`` set.
        if not (os.environ.get("DEVIN_API_KEY") or os.environ.get("WINDSURF_API_KEY")):
            logger.warning(
                "DevinTerminalAdapter: neither DEVIN_API_KEY nor WINDSURF_API_KEY is set "
                "and no `devin auth login` cache has been confirmed - spawn may fail "
                "with an authentication error.",
            )

        cmd: list[str] = [
            "devin",
            _PERMISSION_MODE_FLAG,
            _PERMISSION_MODE_BYPASS,
        ]
        # Devin's CLI accepts ``--model`` only when a model is selected;
        # passing an empty string trips the validator, so guard on the
        # bernstein-side default before forwarding.
        if model_config.model:
            cmd.extend(["--model", model_config.model])
        # ``--print`` accepts an inline prompt as the next positional
        # argument. Keep the prompt last so it's easy to spot in logs.
        cmd.extend([_NON_INTERACTIVE_FLAG, full_prompt])

        # Wrap with bernstein-worker for process visibility (bernstein ps).
        pid_dir = workdir / ".sdd" / "runtime" / "pids"
        wrapped_cmd = build_worker_cmd(
            cmd,
            role=session_id.rsplit("-", 1)[0],
            session_id=session_id,
            pid_dir=pid_dir,
            workdir=workdir,
            log_path=log_path,
            model=model_config.model,
        )

        # ``DEVIN_API_KEY`` is the documented REST-API credential; the
        # CLI also reads ``WINDSURF_API_KEY`` for the Windsurf-bundled
        # distribution, and ``DEVIN_ORG_ID`` scopes the session to a
        # specific organisation when the API key has cross-org access.
        env = build_filtered_env(
            [
                "DEVIN_API_KEY",
                "DEVIN_ORG_ID",
                "WINDSURF_API_KEY",
            ]
        )
        with log_path.open("w") as log_file:
            try:
                proc = subprocess.Popen(
                    wrapped_cmd,
                    cwd=workdir,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                msg = (
                    "devin not found in PATH. "
                    "Install: curl -fsSL https://cli.devin.ai/install.sh | bash "
                    "(see https://cli.devin.ai/docs/reference/commands)"
                )
                raise RuntimeError(msg) from exc
            except PermissionError as exc:
                raise RuntimeError(f"Permission denied executing devin: {exc}") from exc

        self._probe_fast_exit(proc, log_path, provider_name="devin")

        result = SpawnResult(pid=proc.pid, log_path=log_path, proc=proc)
        if timeout_seconds > 0:
            result.timeout_timer = self._start_timeout_watchdog(proc.pid, timeout_seconds, session_id)
        return result

    def name(self) -> str:
        """Return the human-readable adapter name.

        The slug ``devin_terminal`` matches the registry key (whenever
        a follow-up patch wires it into ``adapters/registry.py``) and
        disambiguates this adapter from any future cloud-Devin shim
        that might surface as ``devin``.
        """
        return "devin_terminal"
