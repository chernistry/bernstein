"""CLM sovereign LLM adapter — drives a customer-side CLM gateway.

Some sovereign-AI vendors deploy a customer-side Cyber Language Model
(CLM) served behind NVIDIA NIM, which exposes an OpenAI-compatible HTTP API
(TensorRT-LLM + Triton). This adapter spawns ``aider`` configured to
talk to that gateway via ``OPENAI_API_BASE`` / ``OPENAI_API_KEY``,
unlocking Bernstein's HMAC audit chain, lineage trail, and
fingerprint memoisation for engineering workflows against CLM.

Phase 1 — adapter MVP. Phase 2 partial (this module) wires the
per-agent tool allowlist (T578) into the OpenAI-compatible
``tools=[]`` request shape, refuses spawns that trip the lethal-
trifecta capability matrix, and exposes a streaming-assembly helper
whose lineage payload always carries the full response — never just
the first chunk. mTLS support is deferred to Phase 2.5 once
cluster-mtls-transport's shared ``httpx.SSLContext`` plumbing lands.
See ``docs/adapters/clm.md`` for configuration.
"""

from __future__ import annotations

import json
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
from bernstein.core.agents.spawner_warm_pool import parse_tool_allowlist_env

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from bernstein.core.models import ModelConfig
    from bernstein.core.security.capability_matrix import CapabilityRegistry

logger = logging.getLogger(__name__)

# Env-var keys that scope the adapter to a customer's CLM gateway.
# CLM_TOKEN is a customer-issued scoped JWT — never log, never persist.
CLM_ENDPOINT_ENV = "CLM_ENDPOINT"
CLM_TOKEN_ENV = "CLM_TOKEN"
CLM_MODEL_ENV = "CLM_MODEL"
CLM_TIMEOUT_ENV = "CLM_REQUEST_TIMEOUT_SECONDS"
CLM_MAX_RETRIES_ENV = "CLM_MAX_RETRIES"

# Forwarded into the spawned subprocess as the JSON-encoded OpenAI tools
# array (one entry per allow-listed tool). A downstream wrapper (or an
# in-process HTTP client when we move off aider) reads this and embeds
# it as ``tools=[...]`` on the chat-completions request.
CLM_TOOLS_SCHEMA_ENV = "CLM_TOOLS_SCHEMA"

# Stable adapter token used by the lethal-trifecta capability matrix.
# Matches the row registered in ``templates/capabilities/adapters.yaml``.
_ADAPTER_CAPABILITY_TOKEN = "adapter.clm"

_DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
_DEFAULT_MAX_RETRIES = 2


class ClmConfigError(SpawnError):
    """Raised when required CLM_* configuration is missing or malformed."""


@dataclass(frozen=True)
class ClmConfig:
    """Resolved CLM gateway configuration.

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
                f"CLM adapter requires {', '.join(missing)} to be set; see docs/adapters/clm.md for the env-var bundle."
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


def build_openai_tools_schema(allowlist: Sequence[str]) -> list[dict[str, Any]]:
    """Translate a Bernstein tool allowlist into the OpenAI ``tools=[]`` shape.

    NIM exposes the OpenAI-compatible tools API; the per-spawn allowlist
    (``BERNSTEIN_TOOL_ALLOWLIST``) caps which tools the model is allowed
    to call. The schemas are intentionally minimal — tool descriptions
    and JSON-Schema parameter shapes are owned by the catalog, not the
    adapter; we forward only the names so the catalog stays the single
    source of truth and the adapter cannot accidentally widen them.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Bernstein scoped tool: {tool_name}",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
            },
        }
        for tool_name in allowlist
    ]


@dataclass(frozen=True)
class StreamingChunk:
    """One assistant streaming event emitted by the gateway."""

    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    finish_reason: str | None = None


@dataclass(frozen=True)
class StreamingLineagePayload:
    """Streaming response state captured for the lineage record.

    The contract this dataclass defends, asserted by the regression
    test, is that lineage records carry the *full* assembled response
    even when streaming is on — never just the first chunk.

    Attributes:
        content: Full assistant message body, joined from every chunk.
        tool_calls: Every assistant tool call seen across the stream.
        finish_reason: Final ``finish_reason`` reported by the gateway.
        chunk_count: Total number of chunks observed (for drift signal).
    """

    content: str
    tool_calls: tuple[dict[str, Any], ...]
    finish_reason: str | None
    chunk_count: int


def assemble_streaming_response(events: Iterable[StreamingChunk]) -> StreamingLineagePayload:
    """Fold a streaming event sequence into the full response payload.

    Done in adapter code (not delegated to the SDK) because the lineage
    contract requires the full body, and SDK iterators have historically
    been the source of "first-chunk-only" lineage bugs — see the Phase 2
    streaming regression test.
    """
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish_reason: str | None = None
    count = 0
    for event in events:
        count += 1
        if event.content:
            content_parts.append(event.content)
        if event.tool_calls:
            tool_calls.extend(event.tool_calls)
        if event.finish_reason is not None:
            finish_reason = event.finish_reason
    return StreamingLineagePayload(
        content="".join(content_parts),
        tool_calls=tuple(tool_calls),
        finish_reason=finish_reason,
        chunk_count=count,
    )


def _evaluate_lethal_trifecta(
    allowlist: Sequence[str],
    *,
    workdir: Path | None = None,
    registry: CapabilityRegistry | None = None,
) -> None:
    """Refuse spawns whose adapter-token + tool chain unions all three capabilities.

    The matrix already considers ``adapter.clm`` as carrying
    ``private_data + external_comm`` (it dials a customer gateway with
    operator-shared prompts). Adding any tool tagged ``untrusted_input``
    therefore unions the full trifecta. Enforcement runs *before* the
    CLM call is made, per the Phase 2 acceptance criterion.

    Only the *declared* subset of the chain is evaluated — undeclared
    tools default-deny in the matrix but should not block a spawn here
    (the spawner_core path surfaces them as warnings via the audit CLI),
    matching the policy already used in :mod:`spawner_core`.
    """
    from bernstein.core.security.capability_matrix import (
        CapabilityRegistry,
        LethalTrifectaError,
    )

    reg = registry if registry is not None else CapabilityRegistry.load_default(workdir=workdir)
    chain = [_ADAPTER_CAPABILITY_TOKEN, *allowlist]
    declared = [t for t in chain if t in reg.tools]
    if not declared:
        return
    decision = reg.evaluate_chain(declared)
    if not decision.allowed:
        err = LethalTrifectaError(decision)
        raise SpawnError(f"lethal trifecta: {decision.reason}") from err


class ClmAdapter(CLIAdapter):
    """Spawn ``aider`` against a CLM (NIM) OpenAI-compatible gateway.

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

        allowlist = parse_tool_allowlist_env() or []
        _evaluate_lethal_trifecta(allowlist, workdir=workdir)
        tools_schema = build_openai_tools_schema(allowlist)

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
        if tools_schema:
            env[CLM_TOOLS_SCHEMA_ENV] = json.dumps(tools_schema, separators=(",", ":"))

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
