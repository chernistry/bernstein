"""Unit tests for ClmAdapter (CLM sovereign LLM gateway)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.base import SpawnError
from bernstein.adapters.clm import (
    CLM_ENDPOINT_ENV,
    CLM_MODEL_ENV,
    CLM_TOKEN_ENV,
    CLM_TOOLS_SCHEMA_ENV,
    ClmAdapter,
    ClmConfig,
    ClmConfigError,
    StreamingChunk,
    assemble_streaming_response,
    build_openai_tools_schema,
)
from tests.unit._adapter_test_helpers import inner_cmd, make_popen_mock

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.usefixtures("no_watchdog_threads")


_ENV_BUNDLE = {
    CLM_ENDPOINT_ENV: "https://clm.internal.example/v1/",
    CLM_TOKEN_ENV: "scoped-jwt-customer-001",
    CLM_MODEL_ENV: "clm-7b-instruct",
    "PATH": "/usr/bin",
}


def test_spawn_request_shape_matches_openai_compat(tmp_path: Path) -> None:
    """Spawn-command shape matches the OpenAI-compatible wire format NIM exposes."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(700)

    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", _ENV_BUNDLE, clear=True),
    ):
        adapter.spawn(
            prompt="refactor sigma rules",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-s1",
        )

    inner = inner_cmd(popen.call_args.args[0])
    assert inner[0] == "aider"
    assert "--model" in inner
    assert inner[inner.index("--model") + 1] == "openai/clm-7b-instruct"
    assert inner[inner.index("--message") + 1] == "refactor sigma rules"

    env = popen.call_args.kwargs.get("env", {})
    assert env["OPENAI_API_BASE"] == "https://clm.internal.example/v1/"


def test_authorization_header_uses_scoped_token_not_master(tmp_path: Path) -> None:
    """The scoped CLM_TOKEN — never an operator master key — is forwarded as OPENAI_API_KEY."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(701)

    env_with_master = {
        **_ENV_BUNDLE,
        "ANTHROPIC_API_KEY": "master-anthropic-do-not-leak",
        "OPENAI_API_KEY": "master-openai-do-not-leak",
    }

    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_master, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-s2",
        )

    env = popen.call_args.kwargs.get("env", {})
    assert env["OPENAI_API_KEY"] == "scoped-jwt-customer-001"
    assert "ANTHROPIC_API_KEY" not in env
    serialized = json.dumps(env)
    assert "master-anthropic-do-not-leak" not in serialized
    assert "master-openai-do-not-leak" not in serialized


def test_missing_endpoint_raises_typed_error(tmp_path: Path) -> None:
    """Missing CLM_ENDPOINT surfaces a typed ClmConfigError, not a silent pass."""
    adapter = ClmAdapter()
    incomplete = {k: v for k, v in _ENV_BUNDLE.items() if k != CLM_ENDPOINT_ENV}
    with (
        patch.dict("os.environ", incomplete, clear=True),
        pytest.raises(ClmConfigError, match=CLM_ENDPOINT_ENV),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-missing-endpoint",
        )


def test_missing_cli_raises_runtime_error(tmp_path: Path) -> None:
    """Missing aider binary produces a typed RuntimeError, not a silent pass."""
    adapter = ClmAdapter()
    with (
        patch.dict("os.environ", _ENV_BUNDLE, clear=True),
        patch(
            "bernstein.adapters.clm.subprocess.Popen",
            side_effect=FileNotFoundError("No such file"),
        ),
        pytest.raises(RuntimeError, match="aider not found"),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-missing-cli",
        )


def test_name_returns_clm() -> None:
    assert ClmAdapter().name() == "clm"


def test_config_from_env_parses_optional_overrides() -> None:
    """Optional CLM_REQUEST_TIMEOUT_SECONDS / CLM_MAX_RETRIES override defaults."""
    cfg = ClmConfig.from_env(
        {
            **_ENV_BUNDLE,
            "CLM_REQUEST_TIMEOUT_SECONDS": "120",
            "CLM_MAX_RETRIES": "5",
        }
    )
    assert cfg.endpoint == "https://clm.internal.example/v1/"
    assert cfg.request_timeout_seconds == 120
    assert cfg.max_retries == 5


# ---------------------------------------------------------------------------
# Phase 2 partial — tool-calling allowlist + lethal-trifecta enforcement
# ---------------------------------------------------------------------------


def test_build_openai_tools_schema_emits_function_entries() -> None:
    schema = build_openai_tools_schema(["fs.read", "git.commit"])
    assert [entry["type"] for entry in schema] == ["function", "function"]
    names = [entry["function"]["name"] for entry in schema]
    assert names == ["fs.read", "git.commit"]
    for entry in schema:
        assert entry["function"]["parameters"]["type"] == "object"


def test_spawn_forwards_tool_allowlist_as_openai_tools_array(tmp_path: Path) -> None:
    """The per-spawn allowlist (T578) materialises as OpenAI ``tools=[]`` schema in the env."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(710)
    env_with_allowlist = {
        **_ENV_BUNDLE,
        "BERNSTEIN_TOOL_ALLOWLIST": "fs.read,git.commit",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_allowlist, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-tools",
        )

    env = popen.call_args.kwargs.get("env", {})
    assert CLM_TOOLS_SCHEMA_ENV in env
    schema = json.loads(env[CLM_TOOLS_SCHEMA_ENV])
    assert [entry["function"]["name"] for entry in schema] == ["fs.read", "git.commit"]


def test_spawn_omits_tools_schema_env_when_no_allowlist(tmp_path: Path) -> None:
    """No allowlist → no ``tools=[]`` env, so the gateway sees the unconstrained default."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(711)
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", _ENV_BUNDLE, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-no-tools",
        )

    env = popen.call_args.kwargs.get("env", {})
    assert CLM_TOOLS_SCHEMA_ENV not in env


def test_spawn_refuses_lethal_trifecta_chain(tmp_path: Path) -> None:
    """An allowlist that unions ``private_data + untrusted_input + external_comm`` is denied before the CLM call."""
    adapter = ClmAdapter()
    env_with_lethal_chain = {
        **_ENV_BUNDLE,
        # adapter.clm carries [private_data, external_comm]; web.fetch
        # adds [untrusted_input, external_comm] → full trifecta → deny.
        "BERNSTEIN_TOOL_ALLOWLIST": "web.fetch",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen") as popen,
        patch.dict("os.environ", env_with_lethal_chain, clear=True),
        pytest.raises(SpawnError, match="lethal trifecta"),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-trifecta",
        )
    assert not popen.called, "trifecta refusal must run BEFORE the gateway call"


# ---------------------------------------------------------------------------
# Phase 2 partial — streaming verification regression
# ---------------------------------------------------------------------------


def test_streaming_lineage_carries_full_response_not_first_chunk() -> None:
    """Lineage payload assembles every chunk's content, never just the first.

    This is the regression test the ticket calls out by name:
    streaming bugs historically captured only ``events[0].content`` for
    lineage. We feed 50+ chunks and assert the full body is preserved.
    """
    body = [f"chunk-{i}-payload " for i in range(50)]
    events = [StreamingChunk(content=part) for part in body]
    events.append(StreamingChunk(finish_reason="stop"))

    payload = assemble_streaming_response(events)

    assert payload.content == "".join(body)
    assert payload.chunk_count == len(events)
    assert payload.finish_reason == "stop"
    assert payload.content != events[0].content
    assert "chunk-49-payload" in payload.content


def test_streaming_lineage_captures_tool_calls_across_chunks() -> None:
    events = [
        StreamingChunk(content="thinking..."),
        StreamingChunk(tool_calls=({"id": "c1", "name": "fs.read"},)),
        StreamingChunk(tool_calls=({"id": "c2", "name": "git.commit"},)),
        StreamingChunk(finish_reason="tool_calls"),
    ]
    payload = assemble_streaming_response(events)
    assert [c["id"] for c in payload.tool_calls] == ["c1", "c2"]
    assert payload.finish_reason == "tool_calls"
