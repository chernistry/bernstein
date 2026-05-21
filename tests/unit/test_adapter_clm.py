"""Unit tests for ClmAdapter (CLM sovereign LLM gateway)."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.base import SpawnError
from bernstein.adapters.clm import (
    CLM_CA_FILE_ENV,
    CLM_CERT_FILE_ENV,
    CLM_ENDPOINT_ENV,
    CLM_KEY_FILE_ENV,
    CLM_MODEL_ENV,
    CLM_TOKEN_ENV,
    CLM_TOOLS_SCHEMA_ENV,
    CLM_VERIFY_MODE_ENV,
    ClmAdapter,
    ClmConfig,
    ClmConfigError,
    StreamingChunk,
    StreamingLineagePayload,
    assemble_streaming_response,
    build_openai_tools_schema,
    parse_tool_allowlist_env,
    redactable_clm_env_keys,
    tls_config_from_env,
)
from tests.unit._adapter_test_helpers import inner_cmd, make_popen_mock

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
    """The scoped CLM_TOKEN - never an operator master key - is forwarded as OPENAI_API_KEY."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(701)

    env_with_master = _ENV_BUNDLE | {
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
        _ENV_BUNDLE
        | {
            "CLM_REQUEST_TIMEOUT_SECONDS": "120",
            "CLM_MAX_RETRIES": "5",
        }
    )
    assert cfg.endpoint == "https://clm.internal.example/v1/"
    assert cfg.request_timeout_seconds == 120
    assert cfg.max_retries == 5


# ---------------------------------------------------------------------------
# Phase 2 partial - tool-calling allowlist + lethal-trifecta enforcement
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
    env_with_allowlist = _ENV_BUNDLE | {
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
    env_with_lethal_chain = _ENV_BUNDLE | {
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
# Phase 2 partial - streaming verification regression
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


# ---------------------------------------------------------------------------
# Phase 2.5 - opt-in mTLS to the customer gateway
# ---------------------------------------------------------------------------


def _write_pem_stub(path: Path, label: str) -> None:
    """Write a syntactically-valid PEM placeholder for path-validation only.

    ``tls_config_from_env`` calls ``TLSConfig.validate_paths`` which only
    asserts the files *exist*; integration tests cover real handshakes
    against properly-signed certs. Keeping the unit tier free of x509
    machinery avoids the per-test ~80ms RSA-keygen tax.
    """
    path.write_text(f"-----BEGIN {label}-----\nstub\n-----END {label}-----\n", encoding="utf-8")


def test_tls_config_from_env_returns_none_when_unset() -> None:
    """Operator opted out of mTLS - adapter must keep plain-HTTPS behaviour."""
    assert tls_config_from_env({}) is None
    # PATH presence alone should not flip mTLS on.
    assert tls_config_from_env({"PATH": "/usr/bin"}) is None


def test_tls_config_from_env_partial_triple_raises(tmp_path: Path) -> None:
    cert = tmp_path / "client.crt"
    _write_pem_stub(cert, "CERTIFICATE")
    with pytest.raises(ClmConfigError, match=r"CLM_KEY_FILE.*CLM_CA_FILE"):
        tls_config_from_env({CLM_CERT_FILE_ENV: str(cert)})


def test_tls_config_from_env_full_triple_returns_validated_config(tmp_path: Path) -> None:
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path, label in ((cert, "CERTIFICATE"), (key, "PRIVATE KEY"), (ca, "CERTIFICATE")):
        _write_pem_stub(path, label)
    cfg = tls_config_from_env(
        {
            CLM_CERT_FILE_ENV: str(cert),
            CLM_KEY_FILE_ENV: str(key),
            CLM_CA_FILE_ENV: str(ca),
        }
    )
    assert cfg is not None
    assert cfg.cert_file == cert
    assert cfg.key_file == key
    assert cfg.ca_file == ca
    assert cfg.verify_mode == "required"


def test_tls_config_from_env_honours_verify_mode_override(tmp_path: Path) -> None:
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path, label in ((cert, "CERTIFICATE"), (key, "PRIVATE KEY"), (ca, "CERTIFICATE")):
        _write_pem_stub(path, label)
    cfg = tls_config_from_env(
        {
            CLM_CERT_FILE_ENV: str(cert),
            CLM_KEY_FILE_ENV: str(key),
            CLM_CA_FILE_ENV: str(ca),
            CLM_VERIFY_MODE_ENV: "optional",
        }
    )
    assert cfg is not None
    assert cfg.verify_mode == "optional"


def test_tls_config_from_env_rejects_bogus_verify_mode(tmp_path: Path) -> None:
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path, label in ((cert, "CERTIFICATE"), (key, "PRIVATE KEY"), (ca, "CERTIFICATE")):
        _write_pem_stub(path, label)
    with pytest.raises(ClmConfigError, match="CLM_VERIFY_MODE"):
        tls_config_from_env(
            {
                CLM_CERT_FILE_ENV: str(cert),
                CLM_KEY_FILE_ENV: str(key),
                CLM_CA_FILE_ENV: str(ca),
                CLM_VERIFY_MODE_ENV: "trust-me-bro",
            }
        )


def test_tls_config_from_env_missing_files_raises(tmp_path: Path) -> None:
    with pytest.raises(ClmConfigError, match=r"CLM mTLS configuration is invalid"):
        tls_config_from_env(
            {
                CLM_CERT_FILE_ENV: str(tmp_path / "absent.crt"),
                CLM_KEY_FILE_ENV: str(tmp_path / "absent.key"),
                CLM_CA_FILE_ENV: str(tmp_path / "absent.ca"),
            }
        )


def test_spawn_routes_through_launcher_when_mtls_configured(tmp_path: Path) -> None:
    """When the CLM_*_FILE triple is set, the spawn cmd is rewritten to ``python -m clm_tls_launcher aider …``."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(720)

    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path, label in ((cert, "CERTIFICATE"), (key, "PRIVATE KEY"), (ca, "CERTIFICATE")):
        _write_pem_stub(path, label)

    env_with_mtls = _ENV_BUNDLE | {
        CLM_CERT_FILE_ENV: str(cert),
        CLM_KEY_FILE_ENV: str(key),
        CLM_CA_FILE_ENV: str(ca),
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_mtls, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-mtls",
        )

    inner = inner_cmd(popen.call_args.args[0])
    # Launcher prefix: `<python> -m bernstein.adapters.clm_tls_launcher aider …`
    assert inner[0] == sys.executable
    assert inner[1] == "-m"
    assert inner[2] == "bernstein.adapters.clm_tls_launcher"
    assert inner[3] == "aider"
    # Aider's args are preserved unchanged after the launcher prefix.
    assert "--model" in inner
    assert inner[inner.index("--model") + 1] == "openai/clm-7b-instruct"

    env = popen.call_args.kwargs.get("env", {})
    # Cert/key/ca env vars must reach the spawned subprocess so the
    # launcher can rebuild the TLSConfig in-process.
    assert env[CLM_CERT_FILE_ENV] == str(cert)
    assert env[CLM_KEY_FILE_ENV] == str(key)
    assert env[CLM_CA_FILE_ENV] == str(ca)


def test_spawn_skips_launcher_when_mtls_not_configured(tmp_path: Path) -> None:
    """No mTLS env triple → adapter still calls aider directly (no behaviour drift)."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(721)

    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", _ENV_BUNDLE, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-no-mtls",
        )

    inner = inner_cmd(popen.call_args.args[0])
    assert inner[0] == "aider"
    env = popen.call_args.kwargs.get("env", {})
    assert CLM_CERT_FILE_ENV not in env
    assert CLM_KEY_FILE_ENV not in env
    assert CLM_CA_FILE_ENV not in env


def test_spawn_partial_mtls_triple_surfaces_typed_error(tmp_path: Path) -> None:
    """A half-configured mTLS triple is operator error - fail fast, not silently."""
    adapter = ClmAdapter()
    cert = tmp_path / "client.crt"
    _write_pem_stub(cert, "CERTIFICATE")
    env_partial = _ENV_BUNDLE | {CLM_CERT_FILE_ENV: str(cert)}
    with (
        patch.dict("os.environ", env_partial, clear=True),
        pytest.raises(ClmConfigError, match="CLM mTLS"),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-partial-mtls",
        )


def test_redactable_env_keys_includes_token_and_paths() -> None:
    """Audit / lineage scrubbers must see the token plus all three cert paths."""
    keys = redactable_clm_env_keys()
    assert CLM_TOKEN_ENV in keys
    assert CLM_CERT_FILE_ENV in keys
    assert CLM_KEY_FILE_ENV in keys
    assert CLM_CA_FILE_ENV in keys
    # The endpoint and model are *not* redacted - they're operator
    # configuration the audit trail needs to preserve for traceability.
    assert CLM_ENDPOINT_ENV not in keys
    assert CLM_MODEL_ENV not in keys


# ---------------------------------------------------------------------------
# Edge / failure-mode coverage (verify/clm-adapter-edges)
# ---------------------------------------------------------------------------


def test_clm_config_is_truly_frozen_no_late_mutation() -> None:
    """ClmConfig is ``frozen=True`` - late mutation is impossible.

    A late-binding mutation of the resolved config would let one spawn
    leak a token / endpoint into a sibling spawn whose ClmConfig was
    captured earlier.  The frozen dataclass guarantee is the load-bearing
    invariant; this test pins it.
    """
    cfg = ClmConfig(
        endpoint="https://clm.example/v1/",
        token="scoped-jwt-aaa",
        model="clm-7b-instruct",
        request_timeout_seconds=60,
        max_retries=2,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.token = "swap-me-into-another-customer"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.endpoint = "https://attacker.example/v1/"  # type: ignore[misc]
    # Sanity: the read after the failed mutation still returns the original.
    assert cfg.token == "scoped-jwt-aaa"


def test_streaming_chunk_and_payload_are_frozen() -> None:
    """Streaming dataclasses are frozen so a lineage-write race can't rewrite them."""
    chunk = StreamingChunk(content="x", finish_reason="stop")
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.content = "y"  # type: ignore[misc]
    payload = StreamingLineagePayload(content="x", tool_calls=(), finish_reason="stop", chunk_count=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.content = "y"  # type: ignore[misc]


def test_spawn_filters_broad_provider_master_keys(tmp_path: Path) -> None:
    """No host master credential - Anthropic, OpenAI, Azure, AWS, GCP, GitHub - leaks to the subprocess.

    The existing test pinned ``ANTHROPIC_API_KEY`` and ``OPENAI_API_KEY``;
    sovereign-AI deployments routinely co-locate cloud credentials, so we
    explicitly verify the wider blast radius is also clipped.  ``OPENAI_API_KEY``
    is overwritten by the scoped CLM_TOKEN; the rest are dropped entirely.
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(730)
    leaky_env = _ENV_BUNDLE | {
        "ANTHROPIC_API_KEY": "master-anthropic",
        "OPENAI_API_KEY": "master-openai",
        "OPENAI_ORG_ID": "org-master-secret",
        "OPENAI_PROJECT_ID": "proj-master-secret",
        "AZURE_OPENAI_API_KEY": "azure-master",
        "AZURE_OPENAI_ENDPOINT": "https://azure.master.example/",
        "AWS_ACCESS_KEY_ID": "AKIA-master",
        "AWS_SECRET_ACCESS_KEY": "secret-aws-master",
        "AWS_SESSION_TOKEN": "session-aws-master",
        "GOOGLE_APPLICATION_CREDENTIALS": "/etc/master/gcp-key.json",
        "GITHUB_TOKEN": "ghp_master_token",
        "DATABASE_URL": "postgres://master:master@db/master",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", leaky_env, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-leak-broad",
        )

    env = popen.call_args.kwargs.get("env", {})
    # Scoped token survives as the OpenAI bearer credential - never the master.
    assert env["OPENAI_API_KEY"] == "scoped-jwt-customer-001"
    # No other provider master keys made it through.
    for forbidden_key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GITHUB_TOKEN",
        "DATABASE_URL",
    ):
        assert forbidden_key not in env, f"{forbidden_key} leaked into spawn env"
    # Belt-and-braces: master *values* must not appear anywhere in the env.
    serialized = json.dumps(env)
    forbidden_values = [
        "master-anthropic",
        "master-openai",
        "org-master-secret",
        "proj-master-secret",
        "azure-master",
        "AKIA-master",
        "secret-aws-master",
        "session-aws-master",
        "ghp_master_token",
        "/etc/master/gcp-key.json",
    ]
    for value in forbidden_values:
        assert value not in serialized, f"master credential value leaked: {value}"


def test_spawn_propagates_openai_api_timeout_to_subprocess(tmp_path: Path) -> None:
    """The configured request timeout reaches the OpenAI SDK via ``OPENAI_API_TIMEOUT``."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(740)
    env_with_timeout = _ENV_BUNDLE | {
        "CLM_REQUEST_TIMEOUT_SECONDS": "120",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_timeout, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-timeout",
        )

    env = popen.call_args.kwargs.get("env", {})
    assert env.get("OPENAI_API_TIMEOUT") == "120"


def test_clm_request_timeout_seconds_malformed_raises_typed_error() -> None:
    """Non-integer CLM_REQUEST_TIMEOUT_SECONDS surfaces a typed ClmConfigError."""
    bad_env = _ENV_BUNDLE | {"CLM_REQUEST_TIMEOUT_SECONDS": "ten-seconds"}
    with pytest.raises(ClmConfigError, match="CLM_REQUEST_TIMEOUT_SECONDS"):
        ClmConfig.from_env(bad_env)


def test_clm_max_retries_malformed_raises_typed_error() -> None:
    """Non-integer CLM_MAX_RETRIES surfaces a typed ClmConfigError, not a silent default."""
    bad_env = _ENV_BUNDLE | {"CLM_MAX_RETRIES": "twice"}
    with pytest.raises(ClmConfigError, match="CLM_MAX_RETRIES"):
        ClmConfig.from_env(bad_env)


def test_parse_tool_allowlist_env_handles_whitespace_and_empties() -> None:
    """Allowlist parser strips whitespace, drops empty entries, preserves order."""
    with patch.dict("os.environ", {"BERNSTEIN_TOOL_ALLOWLIST": " fs.read , , git.commit ,, "}, clear=True):
        assert parse_tool_allowlist_env() == ["fs.read", "git.commit"]


def test_parse_tool_allowlist_env_returns_none_when_unset() -> None:
    """Unset / whitespace-only allowlist env returns ``None`` (not an empty list).

    The distinction matters: ``None`` means "no caller scoped the spawn"
    whereas ``[]`` would be an explicit empty allowlist.  Spawn defaults
    to ``[]`` on ``None`` via ``parse_tool_allowlist_env() or []``.
    """
    with patch.dict("os.environ", {}, clear=True):
        assert parse_tool_allowlist_env() is None
    with patch.dict("os.environ", {"BERNSTEIN_TOOL_ALLOWLIST": "   "}, clear=True):
        assert parse_tool_allowlist_env() is None


def test_build_openai_tools_schema_empty_allowlist_returns_empty_list() -> None:
    """An empty allowlist produces an empty schema list - never a wildcard."""
    assert build_openai_tools_schema([]) == []


def test_streaming_lineage_handles_empty_event_stream() -> None:
    """An empty stream yields a payload with no content, no tool calls, finish_reason=None."""
    payload = assemble_streaming_response(iter(()))
    assert payload.content == ""
    assert payload.tool_calls == ()
    assert payload.finish_reason is None
    assert payload.chunk_count == 0


def test_streaming_lineage_finish_reason_is_last_non_none() -> None:
    """When multiple chunks declare a finish_reason, the *last* non-None wins.

    Some gateways emit a placeholder ``finish_reason`` mid-stream and then
    overwrite it on the terminal chunk.  Lineage must record the terminal
    state, not an intermediate one - otherwise the audit trail thinks the
    response was truncated when it really wasn't (and vice versa).
    """
    events = [
        StreamingChunk(content="a", finish_reason=None),
        StreamingChunk(content="b", finish_reason="length"),
        StreamingChunk(content="c", finish_reason=None),
        StreamingChunk(content="d", finish_reason="stop"),
    ]
    payload = assemble_streaming_response(events)
    assert payload.finish_reason == "stop"
    assert payload.content == "abcd"


def test_streaming_lineage_preserves_tool_call_order_across_chunks() -> None:
    """Tool calls accumulate in the order they were emitted across chunks."""
    events = [
        StreamingChunk(tool_calls=({"id": "1", "name": "fs.read"},)),
        StreamingChunk(content="thinking"),
        StreamingChunk(tool_calls=({"id": "2", "name": "git.commit"},)),
        StreamingChunk(tool_calls=({"id": "3", "name": "git.push"},)),
        StreamingChunk(finish_reason="tool_calls"),
    ]
    payload = assemble_streaming_response(events)
    assert [c["id"] for c in payload.tool_calls] == ["1", "2", "3"]


def test_spawn_propagates_verify_mode_when_mtls_active(tmp_path: Path) -> None:
    """``CLM_VERIFY_MODE`` reaches the subprocess so the launcher rebuilds the SSL ctx faithfully."""
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(750)

    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path, label in ((cert, "CERTIFICATE"), (key, "PRIVATE KEY"), (ca, "CERTIFICATE")):
        _write_pem_stub(path, label)

    env_with_mtls = _ENV_BUNDLE | {
        CLM_CERT_FILE_ENV: str(cert),
        CLM_KEY_FILE_ENV: str(key),
        CLM_CA_FILE_ENV: str(ca),
        CLM_VERIFY_MODE_ENV: "optional",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_mtls, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-mtls-verify",
        )

    env = popen.call_args.kwargs.get("env", {})
    assert env[CLM_VERIFY_MODE_ENV] == "optional"


def test_spawn_drops_verify_mode_when_mtls_off(tmp_path: Path) -> None:
    """Without an mTLS triple, ``CLM_VERIFY_MODE`` is *not* allow-listed into the subprocess.

    Forwarding it would imply mTLS is active downstream, which breaks the
    "off by default" contract sovereign-AI customers depend on for staged
    rollouts.
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(751)

    env_with_orphan_verify = _ENV_BUNDLE | {
        CLM_VERIFY_MODE_ENV: "required",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_orphan_verify, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-no-mtls-no-verify",
        )

    env = popen.call_args.kwargs.get("env", {})
    assert CLM_VERIFY_MODE_ENV not in env


def test_tls_config_from_env_disabled_mode_still_returns_config(tmp_path: Path) -> None:
    """``verify_mode='disabled'`` is a valid (non-default) operator choice - config still returned."""
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    ca = tmp_path / "ca.crt"
    for path, label in ((cert, "CERTIFICATE"), (key, "PRIVATE KEY"), (ca, "CERTIFICATE")):
        _write_pem_stub(path, label)
    cfg = tls_config_from_env(
        {
            CLM_CERT_FILE_ENV: str(cert),
            CLM_KEY_FILE_ENV: str(key),
            CLM_CA_FILE_ENV: str(ca),
            CLM_VERIFY_MODE_ENV: "disabled",
        }
    )
    assert cfg is not None
    assert cfg.verify_mode == "disabled"


def test_spawn_undeclared_tools_do_not_block_trifecta(tmp_path: Path) -> None:
    """Undeclared tools (not in the capability registry) default-deny but don't fail spawn here.

    Per the policy comment in ``_evaluate_lethal_trifecta``: undeclared
    tools surface as warnings via the audit CLI; the spawn-time refusal
    only fires when the *declared* subset unions the trifecta.  An
    allowlist of pure noise must therefore complete the spawn (then a
    downstream audit catches the unknowns).
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(760)
    env_with_unknowns = _ENV_BUNDLE | {
        "BERNSTEIN_TOOL_ALLOWLIST": "definitely.not.a.real.tool,still.fictional",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_unknowns, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-unknown-tools",
        )
    assert popen.called, "spawn should not abort on undeclared tools alone"


def test_spawn_overrides_host_openai_api_base_with_clm_endpoint(tmp_path: Path) -> None:
    """A pre-existing host ``OPENAI_API_BASE`` must be overridden by the CLM endpoint.

    If an operator has the OpenAI cloud API base in their shell rc,
    inheritance must not redirect the spawn to it - that would silently
    bypass the customer-side gateway and ship prompts to OpenAI.
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(770)
    env_with_host_base = _ENV_BUNDLE | {
        "OPENAI_API_BASE": "https://api.openai.com/v1",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_host_base, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-base-override",
        )
    env = popen.call_args.kwargs.get("env", {})
    assert env["OPENAI_API_BASE"] == "https://clm.internal.example/v1/"
    # Belt-and-braces: the operator's cloud base must not appear anywhere.
    assert "api.openai.com" not in json.dumps(env)


def test_spawn_tools_schema_env_is_strict_json_with_documented_shape(tmp_path: Path) -> None:
    """The serialized tools schema is well-formed JSON with the documented shape.

    Lineage / audit replay must be able to round-trip this without
    rebuilding the schema from a registry that may have drifted; the
    contract therefore is "the env value parses, lists every requested
    name, and never silently widens parameters".
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(780)
    env_with_allowlist = _ENV_BUNDLE | {
        "BERNSTEIN_TOOL_ALLOWLIST": "fs.read,git.commit,git.push",
    }
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", env_with_allowlist, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-schema-shape",
        )

    env = popen.call_args.kwargs.get("env", {})
    raw = env[CLM_TOOLS_SCHEMA_ENV]
    schema = json.loads(raw)
    assert isinstance(schema, list) and len(schema) == 3
    for entry in schema:
        assert entry["type"] == "function"
        assert set(entry["function"].keys()) == {"name", "description", "parameters"}
        assert entry["function"]["parameters"] == {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    # Each entry's name must correspond to its allowlist entry, in order.
    for name, entry in zip(["fs.read", "git.commit", "git.push"], schema, strict=True):
        assert entry["function"]["name"] == name
        assert name in entry["function"]["description"]


def test_spawn_passes_clm_namespaced_extras_only(tmp_path: Path) -> None:
    """``build_filtered_env`` is invoked with CLM_*-only extras - never operator master keys.

    Asserts the contract from the inside: regardless of host env, the
    ``extra_keys`` argument lists *only* CLM-namespaced keys, so a future
    refactor that accidentally adds ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``
    to extras would be caught here, not in production.
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(790)

    captured: dict[str, object] = {}
    from bernstein.adapters.env_isolation import build_filtered_env as real_build

    def _spy(extra_keys: list[str], **kwargs: object) -> dict[str, str]:
        captured["extra_keys"] = extra_keys.copy()
        return real_build(extra_keys, **kwargs)

    with (
        patch("bernstein.adapters.clm.build_filtered_env", side_effect=_spy),
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock),
        patch.dict("os.environ", _ENV_BUNDLE, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
            session_id="clm-extras-shape",
        )

    extras = captured["extra_keys"]
    assert isinstance(extras, list)
    # Every extra key must start with CLM_ - no provider master snuck in.
    for key in extras:
        assert isinstance(key, str)
        assert key.startswith("CLM_"), f"non-CLM key in extras: {key}"
    # The minimum-viable trio (endpoint, token, model) is always there.
    assert {CLM_ENDPOINT_ENV, CLM_TOKEN_ENV, CLM_MODEL_ENV} <= set(extras)


def test_spawn_clm_endpoint_overrides_inherited_host_endpoint(tmp_path: Path) -> None:
    """Late-binding: even if the host process inherits a stale ``CLM_ENDPOINT``, only the
    *current* call's value is materialised in the spawn env.

    Frozen-config invariant: each ``spawn()`` call rereads env, builds a
    fresh frozen ClmConfig, and routes its endpoint into the subprocess.
    A stale value from a previous spawn must not bleed into the next one.
    """
    adapter = ClmAdapter()
    proc1 = make_popen_mock(800)
    proc2 = make_popen_mock(801)

    env_a = _ENV_BUNDLE | {CLM_ENDPOINT_ENV: "https://customer-a.example/v1/"}
    env_b = _ENV_BUNDLE | {CLM_ENDPOINT_ENV: "https://customer-b.example/v1/"}

    with patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc1) as popen:
        with patch.dict("os.environ", env_a, clear=True):
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
                session_id="clm-cust-a",
            )
        captured_a = popen.call_args.kwargs.get("env", {}).get("OPENAI_API_BASE")
    with patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc2) as popen:
        with patch.dict("os.environ", env_b, clear=True):
            adapter.spawn(
                prompt="hello",
                workdir=tmp_path,
                model_config=ModelConfig(model="clm-7b-instruct", effort="medium"),
                session_id="clm-cust-b",
            )
        captured_b = popen.call_args.kwargs.get("env", {}).get("OPENAI_API_BASE")

    assert captured_a == "https://customer-a.example/v1/"
    assert captured_b == "https://customer-b.example/v1/"


def test_spawn_uses_model_config_override_when_provided(tmp_path: Path) -> None:
    """``model_config.model`` overrides the env-default ``CLM_MODEL`` per-spawn.

    Routing per-task is core to the orchestrator; if the env wins over an
    explicit override, the orchestrator can't pin different models for
    different agents talking to the same gateway.
    """
    adapter = ClmAdapter()
    proc_mock = make_popen_mock(810)
    with (
        patch("bernstein.adapters.clm.subprocess.Popen", return_value=proc_mock) as popen,
        patch.dict("os.environ", _ENV_BUNDLE, clear=True),
    ):
        adapter.spawn(
            prompt="hello",
            workdir=tmp_path,
            model_config=ModelConfig(model="clm-13b-coder", effort="high"),
            session_id="clm-model-override",
        )
    inner = inner_cmd(popen.call_args.args[0])
    assert inner[inner.index("--model") + 1] == "openai/clm-13b-coder"
