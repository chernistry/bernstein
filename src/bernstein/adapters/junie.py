"""JetBrains Junie CLI adapter.

`Junie <https://junie.jetbrains.com>`_ is JetBrains' LLM-agnostic AI
coding agent. It ships as a single ``junie`` binary installed via::

    curl -fsSL https://junie.jetbrains.com/install.sh | bash

The installer drops the binary at ``$HOME/.local/bin/junie`` and the
public README documents an interactive surface (``junie`` followed by
natural-language prompts) plus slash commands such as
``/install-github-action`` and ``/feedback``.

For unattended Bernstein runs we use the documented headless surface
``junie run --headless --model <id> --prompt-file <path>``: the prompt
lives in a file under ``.sdd/runtime/`` so multi-line prompts and shell
metacharacters round-trip cleanly, and ``--headless`` suppresses the
interactive TUI so the process exits when the model finishes the
response.  Junie is BYOK across providers (Anthropic, OpenAI, Google,
xAI, OpenRouter, Copilot), so the adapter forwards whichever provider
key the routed model needs and pins the network policy to the
provider-specific endpoint.

Last verified against https://junie.jetbrains.com/ and the upstream
repository https://github.com/jetbrains-junie/junie on 2026-05-06.
The CLI flag set is still in beta - if the public surface drifts,
update :data:`_HEADLESS_FLAG` / :data:`_PROMPT_FILE_FLAG` here and the
matching assertions in ``tests/unit/test_adapter_junie.py``.
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

# Headless / prompt-file flags from the JetBrains Junie CLI spec
# (junie.jetbrains.com/, 2026-05-06).  Centralised so the test suite
# pins a single source of truth and a CLI drift surfaces in one diff.
_RUN_SUBCOMMAND = "run"
_HEADLESS_FLAG = "--headless"
_MODEL_FLAG = "--model"
_PROMPT_FILE_FLAG = "--prompt-file"

# Map ``JUNIE_PROVIDER`` env values (and falsy -> default route) to the
# corresponding provider-key env var Junie expects in its subprocess.
# Keys mirror the BYOK providers documented at junie.jetbrains.com.
_PROVIDER_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "copilot": "GH_COPILOT_TOKEN",
    "mistral": "MISTRAL_API_KEY",
}

# Provider-host map for the network-policy allow-list. Falls back to
# the empty tuple (no remote allow-listing) when the routed provider is
# unknown - the policy then defers to the global default.
_PROVIDER_ENDPOINTS: dict[str, tuple[tuple[str, int], ...]] = {
    "anthropic": (("api.anthropic.com", 443),),
    "openai": (("api.openai.com", 443),),
    "google": (("generativelanguage.googleapis.com", 443),),
    "gemini": (("generativelanguage.googleapis.com", 443),),
    "xai": (("api.x.ai", 443),),
    "openrouter": (("openrouter.ai", 443),),
    "copilot": (("api.githubcopilot.com", 443),),
    "mistral": (("api.mistral.ai", 443),),
}

# JetBrains Account / Junie API key - always forwarded so the binary
# can resolve account-level entitlements regardless of routed provider.
_JUNIE_ACCOUNT_KEY = "JUNIE_API_KEY"
# Provider-routing override read directly by the binary.
_JUNIE_PROVIDER_KEY = "JUNIE_PROVIDER"


class JunieAdapter(CLIAdapter):
    """Spawn and monitor JetBrains Junie CLI sessions.

    Junie is BYOK and LLM-agnostic; the adapter's external-endpoint
    declaration is populated dynamically from
    ``model_config.provider`` (or ``JUNIE_PROVIDER`` as a fallback) so
    Bernstein's network policy matches whichever upstream API the
    routed model dials.

    The adapter raises only at :meth:`spawn` time - importing this
    module never touches the env, so missing credentials surface as a
    runtime warning when an actual task is dispatched.
    """

    # External endpoints are computed per-spawn via :meth:`_resolve_provider`
    # because Junie can route to any of the BYOK providers. Leave the
    # class-level tuple empty so the base class skips the static check
    # and the per-spawn enforcement is the only authority.
    external_endpoints: tuple[tuple[str, int], ...] = ()

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
        """Launch a one-shot Junie headless session.

        Args:
            prompt: Task prompt - written to a per-session file and
                passed to Junie via ``--prompt-file``.
            workdir: Working directory; Junie treats this as the
                project root.
            model_config: Bernstein model selection.  ``model`` is
                forwarded via ``--model``; ``provider`` (when set on a
                future ``ModelConfig`` revision) drives provider-
                specific env routing - until then we fall back to
                ``$JUNIE_PROVIDER``.
            session_id: Unique session identifier used for log naming
                and the bernstein-worker title.
            mcp_config: Optional MCP server definitions (unused -
                Junie has its own MCP wiring).
            timeout_seconds: Process wall-clock timeout.
            task_scope: Task scope hint (unused by Junie).
            budget_multiplier: Retry budget multiplier (unused).
            system_addendum: Protocol-critical instructions; Junie's
                headless mode reads a single prompt file, so the
                addendum is appended to the prompt body.

        Returns:
            SpawnResult describing the spawned process.

        Raises:
            RuntimeError: The ``junie`` binary is missing from PATH or
                the OS denies execution.
        """
        self.refuse_multimodal_if_needed(multimodal_context)
        provider = self._resolve_provider(model_config)
        # Bind external endpoints for the network policy check based on
        # the routed provider; class-level remains empty so this is the
        # single source of truth.
        self.external_endpoints = _PROVIDER_ENDPOINTS.get(provider, ())
        self.enforce_network_policy()

        runtime_dir = workdir / ".sdd" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = runtime_dir / f"{session_id}.log"
        prompt_path = runtime_dir / f"{session_id}-prompt.txt"

        # Junie's headless mode reads the prompt from a file. Multi-line
        # prompts and shell metacharacters survive intact this way; we
        # also append the system addendum because Junie has no separate
        # system-prompt channel in the documented ``run --headless``
        # surface.
        full_prompt = f"{prompt}\n\n{system_addendum}".rstrip() if system_addendum else prompt
        prompt_path.write_text(full_prompt, encoding="utf-8")

        # Surface missing credentials as a warning rather than a hard
        # error: Junie also supports OAuth login via JetBrains Account,
        # so a fresh user could legitimately have no env-var keys set.
        provider_key = _PROVIDER_ENV_KEYS.get(provider)
        if not os.environ.get(_JUNIE_ACCOUNT_KEY) and not (provider_key and os.environ.get(provider_key)):
            logger.warning(
                "JunieAdapter: neither %s nor the routed provider key (%s) is set "
                "and no JetBrains Account OAuth cache has been confirmed - "
                "spawn may fail with an authentication error.",
                _JUNIE_ACCOUNT_KEY,
                provider_key or "<unknown provider>",
            )

        cmd: list[str] = [
            "junie",
            _RUN_SUBCOMMAND,
            _HEADLESS_FLAG,
        ]
        # Only forward ``--model`` when a non-empty model is selected;
        # Junie falls back to its account-default model otherwise. This
        # matches the Devin adapter's defensive model-flag handling.
        if model_config.model:
            cmd.extend([_MODEL_FLAG, model_config.model])
        cmd.extend([_PROMPT_FILE_FLAG, str(prompt_path)])

        # Wrap with bernstein-worker for process visibility (bernstein ps).
        pid_dir = runtime_dir / "pids"
        wrapped_cmd = build_worker_cmd(
            cmd,
            role=session_id.rsplit("-", 1)[0],
            session_id=session_id,
            pid_dir=pid_dir,
            workdir=workdir,
            log_path=log_path,
            model=model_config.model,
        )

        # Always forward JUNIE_API_KEY + JUNIE_PROVIDER plus the
        # provider-specific key for the routed model.  Keep the list
        # minimal so unrelated master credentials (e.g. an
        # ``OPENAI_MASTER_KEY`` used by the orchestrator itself) cannot
        # leak into the agent's environment.
        extra_keys: list[str] = [_JUNIE_ACCOUNT_KEY, _JUNIE_PROVIDER_KEY]
        if provider_key:
            extra_keys.append(provider_key)
        env = build_filtered_env(extra_keys)

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
                    "junie not found in PATH. "
                    "Install: curl -fsSL https://junie.jetbrains.com/install.sh | bash "
                    "(see https://junie.jetbrains.com/)"
                )
                raise RuntimeError(msg) from exc
            except PermissionError as exc:
                raise RuntimeError(f"Permission denied executing junie: {exc}") from exc

        self._probe_fast_exit(proc, log_path, provider_name="junie")

        result = SpawnResult(pid=proc.pid, log_path=log_path, proc=proc)
        if timeout_seconds > 0:
            result.timeout_timer = self._start_timeout_watchdog(proc.pid, timeout_seconds, session_id)
        return result

    def name(self) -> str:
        """Return the registry / metadata adapter name."""
        return "junie"

    @staticmethod
    def _resolve_provider(model_config: ModelConfig) -> str:
        """Resolve the BYOK provider for env routing and policy.

        Precedence:
          1. ``model_config.provider`` if explicitly set on the task
             (a future ``ModelConfig`` revision will surface this).
          2. ``$JUNIE_PROVIDER`` env var (matches the binary's own
             override mechanism).
          3. Empty string when nothing is configured - caller falls
             back to provider-agnostic env routing.

        Returns:
            Lower-cased provider slug (e.g. ``"anthropic"``).  May be
            empty when no provider is configured.
        """
        provider_obj: Any = getattr(model_config, "provider", None)
        if provider_obj is not None:
            # Tolerate both StrEnum values and plain strings.
            provider_value = getattr(provider_obj, "value", str(provider_obj))
            if provider_value:
                return str(provider_value).lower()
        return os.environ.get(_JUNIE_PROVIDER_KEY, "").lower()
