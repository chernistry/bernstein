"""AWS Q Developer CLI adapter (binary: ``q``).

Amazon Q Developer ships a single-binary CLI installed via Homebrew
(``brew install --cask amazon-q``), the AppImage on Linux, or the AWS-hosted
``.deb``/``.rpm`` packages.  Headless invocation matches the documented
non-interactive shape::

    q chat --no-interactive --trust-all-tools "<prompt>"

The agent reads its bearer token from the on-disk login cache that
``q login`` writes - there is no ``Q_API_KEY`` style env var.  The cache
location is platform-dependent (XDG-anchored on Linux/macOS,
``%LOCALAPPDATA%`` on Windows) and Bernstein refuses to spawn when no
plausible cache directory is present, surfacing a clear "run ``q login``"
message rather than letting the CLI dump an authentication stack-trace
into the agent log.

Authentication backends supported by the upstream binary:

* AWS Builder ID (free, personal account).
* IAM Identity Center (enterprise SSO).

Important risk: when the spawn env carries an IAM Identity Center session,
``q``'s tool calls execute with **the user's IAM Identity Center role**.
Routing infra-touching tasks (Terraform plans, AWS resource mutations)
through this adapter therefore inherits that role's permissions -
operators should scope the role narrowly or route those tasks via a
dedicated ``IaCAdapter`` instead.

Project status (2026-05-06): the upstream
`aws/amazon-q-developer-cli <https://github.com/aws/amazon-q-developer-cli>`_
repo declares the project deprecated and rebranded as **Kiro CLI**
(``kiro-cli``, see :mod:`bernstein.adapters.kiro`).  The legacy ``q``
binary continues to ship for existing installs and the documented
``--no-interactive --trust-all-tools`` surface is unchanged; this adapter
targets that legacy surface on purpose so users on the original Builder ID
flow keep working without forcing a Kiro migration.

Last verified against:
* https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line.html
* https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-chat.html
* https://github.com/aws/amazon-q-developer-cli (issues #1951, #1995)

verified on 2026-05-06.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bernstein.adapters.base import (
    DEFAULT_TIMEOUT_SECONDS,
    CLIAdapter,
    SpawnError,
    SpawnResult,
    build_worker_cmd,
)
from bernstein.adapters.env_isolation import build_filtered_env

if TYPE_CHECKING:
    from bernstein.core.models import ModelConfig

logger = logging.getLogger(__name__)


# Documented non-interactive flags. ``--no-interactive`` disables the TTY
# prompt and ``--trust-all-tools`` short-circuits per-tool confirmations,
# both required for unattended runs (issue #1951 in the upstream repo
# describes how missing either flag deadlocks the CLI on stdin).
_NON_INTERACTIVE_FLAG = "--no-interactive"
_TRUST_ALL_TOOLS_FLAG = "--trust-all-tools"


class QDevAdapter(CLIAdapter):
    """Spawn and monitor AWS Q Developer (``q``) CLI sessions.

    The adapter intentionally surfaces a hard error when no Q login cache
    is present: ``q chat`` would otherwise emit a multi-line OAuth flow
    into the log file and block waiting for a browser handshake that no
    headless agent can complete.  Operators should run ``q login`` once
    on the host before pointing Bernstein at this adapter.

    Permissions are bound to the underlying AWS Builder ID / IAM Identity
    Center principal - see the module docstring for the role-scoping
    caveat.
    """

    # AWS Q dials home through the regional ``*.amazonaws.com`` plane and
    # the developer-tooling control plane on ``*.aws.dev``.  The wildcards
    # are honoured by ``policy.check`` when the active network policy
    # allows host-pattern entries.
    external_endpoints = (
        ("*.amazonaws.com", 443),
        ("*.aws.dev", 443),
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
        """Launch a one-shot ``q chat`` session.

        Args:
            prompt: Task prompt - passed as the trailing positional after
                the documented ``--no-interactive --trust-all-tools``
                flags.
            workdir: Working directory; ``q`` treats it as the project
                root.
            model_config: Bernstein model selection.  ``q`` exposes model
                routing via Amazon Q-side configuration rather than a
                per-invocation flag, so the requested model is logged for
                observability but not forwarded.
            session_id: Unique session identifier used for log naming and
                the bernstein-worker process title.
            mcp_config: Optional MCP server definitions (unused - Q
                manages MCP via its own ``q mcp`` subcommand).
            timeout_seconds: Process wall-clock timeout.
            task_scope: Task scope hint (unused).
            budget_multiplier: Retry budget multiplier (unused).
            system_addendum: Protocol-critical instructions; ``q`` accepts
                a single positional prompt so the addendum is appended
                inline.

        Returns:
            SpawnResult describing the spawned process.

        Raises:
            SpawnError: No ``q login`` cache was found on the host.
            RuntimeError: The ``q`` binary is missing from PATH or the OS
                denies execution.
        """
        self.refuse_multimodal_if_needed(multimodal_context)
        self.enforce_network_policy()
        log_path = workdir / ".sdd" / "runtime" / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Fail fast when no Q login cache is present - q would otherwise
        # block on an OAuth browser flow that no unattended agent can
        # complete.  This must happen *before* subprocess.Popen so the
        # error never gets buried inside the agent log.
        if not _has_q_login_cache():
            raise SpawnError(
                "AWS Q Developer login cache not found. "
                "Run `q login` once on this host before spawning the q_dev adapter "
                "(token persists in ~/.local/share/amazon-q/ on Linux/macOS or "
                "%LOCALAPPDATA%\\amazon-q\\ on Windows).",
            )

        # ``q`` accepts a single positional prompt; graft the addendum on
        # so completion / heartbeat instructions still reach the agent
        # regardless of system_addendum being empty.
        full_prompt = f"{prompt}\n\n{system_addendum}".rstrip() if system_addendum else prompt

        if model_config.model and model_config.model.lower() != "auto":
            logger.info(
                "QDevAdapter: requested model %s for session %s; AWS Q exposes model "
                "selection via account-side configuration, not a per-run flag",
                model_config.model,
                session_id,
            )
        if mcp_config:
            logger.debug("QDevAdapter ignoring runtime MCP config injection for session %s", session_id)

        cmd: list[str] = [
            "q",
            "chat",
            _NON_INTERACTIVE_FLAG,
            _TRUST_ALL_TOOLS_FLAG,
            full_prompt,
        ]

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

        # Q reads its bearer token from the on-disk login cache, NOT from
        # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY style env vars.  We
        # forward AWS_PROFILE / AWS_REGION so users with multiple
        # Identity Center sessions can pin one without leaking root
        # credentials, but never the long-lived access keys.
        env = build_filtered_env(["AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION"])

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
                    "q not found in PATH. Install AWS Q Developer CLI: "
                    "`brew install --cask amazon-q` (macOS) or see "
                    "https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line.html"
                )
                raise RuntimeError(msg) from exc
            except PermissionError as exc:
                raise RuntimeError(f"Permission denied executing q: {exc}") from exc

        self._probe_fast_exit(proc, log_path, provider_name="q_dev")

        result = SpawnResult(pid=proc.pid, log_path=log_path, proc=proc)
        if timeout_seconds > 0:
            result.timeout_timer = self._start_timeout_watchdog(proc.pid, timeout_seconds, session_id)
        return result

    def name(self) -> str:
        """Return the registry slug used by the orchestrator and configs."""
        return "q_dev"


def _has_q_login_cache() -> bool:
    """Return True when a plausible ``q login`` cache directory exists.

    We don't validate token freshness here - that's q's job at runtime.
    The check is purely "did anyone log in on this host at least once?"
    so we can fail with a clean error message instead of letting q
    deadlock on the OAuth handshake.

    Linux/macOS look at ``$XDG_DATA_HOME/amazon-q`` (falling back to
    ``~/.local/share/amazon-q``); Windows looks at
    ``%LOCALAPPDATA%\\amazon-q``.  Either layout counts as logged-in.
    """
    candidates: list[Path] = []
    home = Path.home()

    if platform.system() == "Windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "amazon-q")
        candidates.append(home / "AppData" / "Local" / "amazon-q")
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            candidates.append(Path(xdg_data) / "amazon-q")
        # macOS users sometimes end up with the legacy ~/.amazon-q layout.
        candidates.extend(
            (
                home / ".local" / "share" / "amazon-q",
                home / ".amazon-q",
            )
        )

    return any(p.exists() for p in candidates)
