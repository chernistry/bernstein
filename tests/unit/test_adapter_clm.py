"""Unit tests for ClmAdapter (CLM sovereign LLM gateway)."""

from __future__ import annotations

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
    assemble_streaming_response,
    build_openai_tools_schema,
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


# ---------------------------------------------------------------------------
# Phase 2.5 — opt-in mTLS to the customer gateway
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
    """Operator opted out of mTLS — adapter must keep plain-HTTPS behaviour."""
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

    env_with_mtls = {
        **_ENV_BUNDLE,
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
    """A half-configured mTLS triple is operator error — fail fast, not silently."""
    adapter = ClmAdapter()
    cert = tmp_path / "client.crt"
    _write_pem_stub(cert, "CERTIFICATE")
    env_partial = {**_ENV_BUNDLE, CLM_CERT_FILE_ENV: str(cert)}
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
    # The endpoint and model are *not* redacted — they're operator
    # configuration the audit trail needs to preserve for traceability.
    assert CLM_ENDPOINT_ENV not in keys
    assert CLM_MODEL_ENV not in keys
