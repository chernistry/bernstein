"""Unit tests for ClmAdapter (Dream-CLM sovereign LLM gateway)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from bernstein.core.models import ModelConfig

from bernstein.adapters.clm import (
    CLM_ENDPOINT_ENV,
    CLM_MODEL_ENV,
    CLM_TOKEN_ENV,
    ClmAdapter,
    ClmConfig,
    ClmConfigError,
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
