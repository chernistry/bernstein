"""Dream-CLM sovereign LLM adapter — drives a customer-side CLM gateway.

Dream Security ships a proprietary Cyber Language Model (CLM) family
served behind NVIDIA NIM, which exposes an OpenAI-compatible HTTP API
(TensorRT-LLM + Triton). This adapter spawns ``aider`` configured to
talk to that gateway via ``OPENAI_API_BASE`` / ``OPENAI_API_KEY``,
unlocking Bernstein's HMAC audit chain, lineage trail, and
fingerprint memoisation for engineering workflows against CLM.

Phase 1 — adapter MVP. Phase 2 (mTLS, tool-calling, streaming
regression) is deferred until the cluster-mtls-transport ticket
lands. See ``docs/adapters/clm.md`` for configuration.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
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
    from collections.abc import Mapping
    from pathlib import Path

    from bernstein.core.models import ModelConfig

logger = logging.getLogger(__name__)

# Env-var keys that scope the adapter to a customer's CLM gateway.
# CLM_TOKEN is a customer-issued scoped JWT — never log, never persist.
CLM_ENDPOINT_ENV = "CLM_ENDPOINT"
CLM_TOKEN_ENV = "CLM_TOKEN"
CLM_MODEL_ENV = "CLM_MODEL"
CLM_TIMEOUT_ENV = "CLM_REQUEST_TIMEOUT_SECONDS"
CLM_MAX_RETRIES_ENV = "CLM_MAX_RETRIES"

_DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
_DEFAULT_MAX_RETRIES = 2


class ClmConfigError(SpawnError):
    """Raised when required CLM_* configuration is missing or malformed."""


@dataclass(frozen=True)
class ClmConfig:
    """Resolved Dream-CLM gateway configuration.

    Attributes:
        endpoint: Customer-side gateway base URL (OpenAI-compatible).
        token: Scoped JWT used as Bearer credential. Treated as opaque.
        model: Model id passed through to the gateway.
        request_timeout_seconds: Per-request HTTP timeout for the SDK.
        max_retries: SDK-level retry budget for transient errors.
    """

    endpoint: str
    token: str
    model: str
    request_timeout_seconds: int
    max_retries: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ClmConfig:
        """Build a config from CLM_* env vars, raising if any required key is missing."""
        source: Mapping[str, str] = env if env is not None else os.environ
        endpoint = source.get(CLM_ENDPOINT_ENV, "").strip()
        token = source.get(CLM_TOKEN_ENV, "").strip()
        model = source.get(CLM_MODEL_ENV, "").strip()

        missing = [
            k
            for k, v in (
                (CLM_ENDPOINT_ENV, endpoint),
                (CLM_TOKEN_ENV, token),
                (CLM_MODEL_ENV, model),
            )
            if not v
        ]
        if missing:
            raise ClmConfigError(
                f"Dream-CLM adapter requires {', '.join(missing)} to be set; "
                "see docs/adapters/clm.md for the env-var bundle."
            )

        return cls(
            endpoint=endpoint,
            token=token,
            model=model,
            request_timeout_seconds=_int_env(source, CLM_TIMEOUT_ENV, _DEFAULT_REQUEST_TIMEOUT_SECONDS),
            max_retries=_int_env(source, CLM_MAX_RETRIES_ENV, _DEFAULT_MAX_RETRIES),
        )


def _int_env(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ClmConfigError(f"{name} must be an integer, got {raw!r}") from exc


class ClmAdapter(CLIAdapter):
    """Spawn ``aider`` against a Dream-CLM (NIM) OpenAI-compatible gateway.

    The adapter is a thin shim: aider handles the OpenAI HTTP wire
    format, while Bernstein's spawner provides lifecycle, timeouts,
    audit chaining, and lineage. Master tokens never leave the
    operator's machine — only the customer-issued CLM_TOKEN is
    forwarded to the spawned subprocess.
    """

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
    ) -> SpawnResult:
        log_path = workdir / ".sdd" / "runtime" / f"{session_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        config = ClmConfig.from_env()
        model_id = model_config.model or config.model

        cmd = [
            "aider",
            "--model",
            f"openai/{model_id}",
            "--message",
            prompt,
            "--yes",
            "--auto-commits",
            "--map-tokens",
            "1024",
            "--no-auto-lint",
        ]

        pid_dir = workdir / ".sdd" / "runtime" / "pids"
        wrapped_cmd = build_worker_cmd(
            cmd,
            role=session_id.rsplit("-", 1)[0],
            session_id=session_id,
            pid_dir=pid_dir,
            workdir=workdir,
            log_path=log_path,
            model=f"clm/{model_id}",
        )

        env = build_filtered_env([CLM_ENDPOINT_ENV, CLM_TOKEN_ENV, CLM_MODEL_ENV])
        # Aider speaks the OpenAI wire format; rewire it onto the CLM
        # gateway via the standard OpenAI env vars. The scoped CLM_TOKEN
        # rides as the Bearer credential — never the operator's master.
        env["OPENAI_API_BASE"] = config.endpoint
        env["OPENAI_API_KEY"] = config.token
        env["OPENAI_API_TIMEOUT"] = str(config.request_timeout_seconds)

        with log_path.open("w") as log_file:
            try:
                proc = subprocess.Popen(
                    wrapped_cmd,
                    cwd=workdir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("aider not found in PATH. Install with: pip install aider-chat") from exc
            except PermissionError as exc:
                raise RuntimeError(f"Permission denied executing aider: {exc}") from exc

        result = SpawnResult(pid=proc.pid, log_path=log_path, proc=proc)
        if timeout_seconds > 0:
            result.timeout_timer = self._start_timeout_watchdog(proc.pid, timeout_seconds, session_id)
        return result

    def name(self) -> str:
        return "clm"
