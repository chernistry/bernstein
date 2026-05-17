"""Tests for the OTLP exporter with GenAI semantic conventions.

These tests use ``opentelemetry-sdk``'s :class:`InMemorySpanExporter` so
they never touch the network.  The optional ``opentelemetry-exporter-otlp-
proto-grpc`` package is exercised only via the env-var path, which is
covered by patching the importable symbol.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from bernstein.core.observability.otlp_exporter import (
    ATTR_GEN_AI_OPERATION_NAME,
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_GEN_AI_TOOL_CALL_ID,
    ATTR_GEN_AI_TOOL_NAME,
    ATTR_GEN_AI_USAGE_COMPLETION_TOKENS,
    ATTR_GEN_AI_USAGE_PROMPT_TOKENS,
    DEFAULT_SERVICE_NAME,
    OTEL_ENDPOINT_ENV,
    OTEL_SERVICE_NAME_ENV,
    GenAIOTLPExporter,
    OTLPExporterConfig,
    get_default_exporter,
    reset_default_exporter,
)


@pytest.fixture
def in_memory_provider() -> Iterator[tuple[TracerProvider, InMemorySpanExporter]]:
    """Provide a fresh tracer provider wired to an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        yield provider, exporter
    finally:
        provider.shutdown()


@pytest.fixture(autouse=True)
def _clear_default_exporter() -> Iterator[None]:
    """Ensure the singleton default exporter does not leak between tests."""
    reset_default_exporter()
    try:
        yield
    finally:
        reset_default_exporter()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_from_env_empty_returns_disabled() -> None:
    """No env var means the config is disabled (endpoint is None)."""
    cfg = OTLPExporterConfig.from_env({})
    assert cfg.endpoint is None
    assert cfg.service_name == DEFAULT_SERVICE_NAME


def test_config_from_env_reads_endpoint_and_service_name() -> None:
    cfg = OTLPExporterConfig.from_env(
        {
            OTEL_ENDPOINT_ENV: "http://otel-collector:4317",
            OTEL_SERVICE_NAME_ENV: "bernstein-prod",
        },
    )
    assert cfg.endpoint == "http://otel-collector:4317"
    assert cfg.service_name == "bernstein-prod"


def test_config_from_env_blank_endpoint_treated_as_unset() -> None:
    """Empty string is intentionally treated as 'not configured'."""
    cfg = OTLPExporterConfig.from_env({OTEL_ENDPOINT_ENV: ""})
    assert cfg.endpoint is None


# ---------------------------------------------------------------------------
# No-op behaviour when endpoint is unset
# ---------------------------------------------------------------------------


def test_exporter_disabled_when_no_endpoint() -> None:
    exporter = GenAIOTLPExporter(OTLPExporterConfig(endpoint=None))
    assert exporter.enabled is False

    # Disabled exporter must still be safe to call.
    with exporter.start_genai_span(system="anthropic", model="claude-sonnet-4-6") as span:
        assert span is None
        exporter.record_usage(span, prompt_tokens=10, completion_tokens=5)
    exporter.shutdown()  # idempotent on disabled


def test_get_default_exporter_no_env_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OTEL_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(OTEL_SERVICE_NAME_ENV, raising=False)
    exporter = get_default_exporter()
    assert exporter.enabled is False
    # Same instance returned on subsequent calls.
    assert get_default_exporter() is exporter


# ---------------------------------------------------------------------------
# GenAI semantic conventions on emitted spans
# ---------------------------------------------------------------------------


def test_span_contains_gen_ai_semantic_conventions(
    in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = in_memory_provider
    otlp = GenAIOTLPExporter(
        OTLPExporterConfig(endpoint=None, service_name="bernstein-test"),
        tracer_provider=provider,
    )
    assert otlp.enabled is True

    with otlp.start_genai_span(
        operation="chat",
        system="anthropic",
        model="claude-sonnet-4-6",
        tool_name="repo.search",
        tool_call_id="call-123",
        extra_attributes={"task.id": "task-007"},
    ) as span:
        assert span is not None
        otlp.record_usage(span, prompt_tokens=128, completion_tokens=64)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    finished = spans[0]
    assert finished.name == "gen_ai.chat"

    attrs = dict(finished.attributes or {})
    assert attrs[ATTR_GEN_AI_SYSTEM] == "anthropic"
    assert attrs[ATTR_GEN_AI_REQUEST_MODEL] == "claude-sonnet-4-6"
    assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "chat"
    assert attrs[ATTR_GEN_AI_TOOL_NAME] == "repo.search"
    assert attrs[ATTR_GEN_AI_TOOL_CALL_ID] == "call-123"
    assert attrs[ATTR_GEN_AI_USAGE_PROMPT_TOKENS] == 128
    assert attrs[ATTR_GEN_AI_USAGE_COMPLETION_TOKENS] == 64
    assert attrs["task.id"] == "task-007"


def test_span_without_tool_attrs_omits_them(
    in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = in_memory_provider
    otlp = GenAIOTLPExporter(
        OTLPExporterConfig(endpoint=None),
        tracer_provider=provider,
    )
    with otlp.start_genai_span(system="openai", model="gpt-4o-mini") as span:
        assert span is not None

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert ATTR_GEN_AI_TOOL_NAME not in attrs
    assert ATTR_GEN_AI_TOOL_CALL_ID not in attrs
    assert ATTR_GEN_AI_USAGE_PROMPT_TOKENS not in attrs


def test_record_usage_with_only_prompt_tokens(
    in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Recording one half of usage must not invent the other."""
    provider, exporter = in_memory_provider
    otlp = GenAIOTLPExporter(
        OTLPExporterConfig(endpoint=None),
        tracer_provider=provider,
    )
    with otlp.start_genai_span(system="anthropic", model="claude") as span:
        otlp.record_usage(span, prompt_tokens=10)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs[ATTR_GEN_AI_USAGE_PROMPT_TOKENS] == 10
    assert ATTR_GEN_AI_USAGE_COMPLETION_TOKENS not in attrs


def test_default_operation_is_chat(
    in_memory_provider: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    provider, exporter = in_memory_provider
    otlp = GenAIOTLPExporter(OTLPExporterConfig(endpoint=None), tracer_provider=provider)
    with otlp.start_genai_span(system="anthropic", model="claude"):
        pass
    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs[ATTR_GEN_AI_OPERATION_NAME] == "chat"


# ---------------------------------------------------------------------------
# Optional-dep guard
# ---------------------------------------------------------------------------


def test_missing_optional_grpc_extra_disables_exporter() -> None:
    """When the gRPC OTLP package is missing, exporter falls back to no-op."""
    import sys

    real_module_name = "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
    # Hide the gRPC OTLP module from a fresh import.
    with patch.dict(sys.modules, {real_module_name: None}):
        exporter = GenAIOTLPExporter(
            OTLPExporterConfig(endpoint="http://collector:4317"),
        )
        # Even with an endpoint configured, the exporter must NOT raise --
        # it should degrade to disabled.
        assert exporter.enabled is False

        # And remain callable.
        with exporter.start_genai_span(system="anthropic", model="claude") as span:
            assert span is None
