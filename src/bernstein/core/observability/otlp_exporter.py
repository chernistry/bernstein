"""OTLP exporter with OpenTelemetry GenAI semantic conventions.

Operators running Bernstein at scale already have an observability stack
(Datadog, Honeycomb, Grafana / Tempo, Elastic, ...).  The default JSONL
writer under ``.sdd/traces/`` is great for local debugging but invisible
to existing dashboards.

This module emits spans over OTLP/gRPC using the OpenTelemetry GenAI
semantic conventions so operator tooling can recognise Bernstein traffic
without bespoke parsing.  When the ``BERNSTEIN_OTEL_ENDPOINT`` env var is
unset (or the optional ``opentelemetry-exporter-otlp-proto-grpc`` package
is not installed) every method becomes a no-op -- the local JSONL store
remains the default destination.

Usage::

    from bernstein.core.observability.otlp_exporter import (
        GenAIOTLPExporter,
        get_default_exporter,
    )

    exporter = get_default_exporter()
    with exporter.start_genai_span(
        operation="chat",
        system="anthropic",
        model="claude-sonnet-4-6",
    ) as span:
        ...  # call the model
        exporter.record_usage(span, prompt_tokens=128, completion_tokens=64)

Pin points:

* Environment variable: ``BERNSTEIN_OTEL_ENDPOINT`` (e.g. ``http://otel-collector:4317``)
* Optional service-name override: ``BERNSTEIN_OTEL_SERVICE_NAME`` (default ``bernstein``)
* Optional install extra: ``pip install 'bernstein[otel]'``

Semantic conventions covered (`OTel GenAI <https://opentelemetry.io/docs/specs/semconv/gen-ai/>`__):

* ``gen_ai.system``
* ``gen_ai.request.model``
* ``gen_ai.operation.name``
* ``gen_ai.usage.prompt_tokens``
* ``gen_ai.usage.completion_tokens``
* ``gen_ai.tool.name``
* ``gen_ai.tool.call.id``
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------


#: Environment variable that toggles the OTLP exporter on.  When unset, the
#: exporter is a no-op so default installs do not require the optional
#: ``opentelemetry-exporter-otlp-proto-grpc`` package.
OTEL_ENDPOINT_ENV = "BERNSTEIN_OTEL_ENDPOINT"

#: Optional override for the ``service.name`` resource attribute.
OTEL_SERVICE_NAME_ENV = "BERNSTEIN_OTEL_SERVICE_NAME"

#: Default service name applied to every emitted span when no override is set.
DEFAULT_SERVICE_NAME = "bernstein"

#: Default GenAI operation name -- ``chat`` covers most adapter calls.
DEFAULT_OPERATION = "chat"

# GenAI semantic-convention attribute names.  Re-exported as module-level
# constants so adapters can apply them without a hard dep on the OTel SDK.
ATTR_GEN_AI_SYSTEM = "gen_ai.system"
ATTR_GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
ATTR_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
ATTR_GEN_AI_USAGE_PROMPT_TOKENS = "gen_ai.usage.prompt_tokens"
ATTR_GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"
ATTR_GEN_AI_TOOL_NAME = "gen_ai.tool.name"
ATTR_GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OTLPExporterConfig:
    """Static configuration for :class:`GenAIOTLPExporter`.

    Attributes:
        endpoint: OTLP/gRPC collector URL (e.g. ``http://otel-collector:4317``).
            ``None`` disables export entirely.
        service_name: ``service.name`` resource attribute.
        insecure: Skip TLS verification on gRPC channel.
        headers: Extra gRPC metadata forwarded to the collector.
        resource_attributes: Static resource attributes merged with the
            default ``service.name``.
    """

    endpoint: str | None
    service_name: str = DEFAULT_SERVICE_NAME
    insecure: bool = True
    headers: dict[str, str] = field(default_factory=dict[str, str])
    resource_attributes: dict[str, str] = field(default_factory=dict[str, str])

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OTLPExporterConfig:
        """Build a config from environment variables.

        Args:
            env: Mapping to read from.  Defaults to ``os.environ``.

        Returns:
            A new :class:`OTLPExporterConfig`.  ``endpoint`` will be ``None``
            (i.e. exporter disabled) when ``BERNSTEIN_OTEL_ENDPOINT`` is not
            set or is empty.
        """
        source = env if env is not None else os.environ
        raw_endpoint = source.get(OTEL_ENDPOINT_ENV) or None
        service_name = source.get(OTEL_SERVICE_NAME_ENV) or DEFAULT_SERVICE_NAME
        return cls(endpoint=raw_endpoint, service_name=service_name)


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


class GenAIOTLPExporter:
    """OTLP/gRPC exporter that tags spans with GenAI semantic conventions.

    The exporter is fail-safe: when ``config.endpoint`` is ``None`` or the
    optional ``opentelemetry-exporter-otlp-proto-grpc`` package is missing,
    every public method is a no-op.  Callers do not need to special-case
    "telemetry disabled" -- they can always invoke ``start_genai_span``
    and friends.

    Args:
        config: Static configuration.  Use :meth:`OTLPExporterConfig.from_env`
            for the standard ``BERNSTEIN_OTEL_ENDPOINT`` flow.
        tracer_provider: Optional pre-built tracer provider.  When supplied,
            the exporter wires its span processor onto this provider instead
            of creating a new one.  Used by the InMemorySpanExporter test
            harness.
    """

    def __init__(
        self,
        config: OTLPExporterConfig | None = None,
        *,
        tracer_provider: Any | None = None,
    ) -> None:
        self._config = config or OTLPExporterConfig.from_env()
        self._tracer: Any | None = None
        self._provider: Any | None = tracer_provider
        self._enabled = False

        if self._config.endpoint is tracer_provider is None:
            # Disabled - keep everything as None so calls become no-ops.
            return

        self._enabled = self._init_tracer()

    # ------------------------------------------------------------------ API

    @property
    def enabled(self) -> bool:
        """Whether the exporter has an active OTLP destination."""
        return self._enabled

    @property
    def config(self) -> OTLPExporterConfig:
        """Effective configuration (after env-var resolution)."""
        return self._config

    @contextlib.contextmanager
    def start_genai_span(
        self,
        *,
        operation: str = DEFAULT_OPERATION,
        system: str,
        model: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        extra_attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[Any]:
        """Open a span tagged with the GenAI semantic conventions.

        Args:
            operation: ``gen_ai.operation.name``.  Defaults to ``"chat"``.
            system: ``gen_ai.system`` (e.g. ``"anthropic"``, ``"openai"``).
            model: ``gen_ai.request.model`` (e.g. ``"claude-sonnet-4-6"``).
            tool_name: Optional ``gen_ai.tool.name`` when the span covers
                a tool invocation.
            tool_call_id: Optional ``gen_ai.tool.call.id``.
            extra_attributes: Additional attributes merged on top of the
                semantic-convention ones.  Useful for ``task.id`` and other
                Bernstein-specific tags.

        Yields:
            The underlying OTel span object, or ``None`` when the exporter
            is disabled.  Tests should not depend on the concrete span type.
        """
        if not self._enabled or self._tracer is None:
            yield None
            return

        attributes: dict[str, Any] = {
            ATTR_GEN_AI_OPERATION_NAME: operation,
            ATTR_GEN_AI_SYSTEM: system,
            ATTR_GEN_AI_REQUEST_MODEL: model,
        }
        if tool_name is not None:
            attributes[ATTR_GEN_AI_TOOL_NAME] = tool_name
        if tool_call_id is not None:
            attributes[ATTR_GEN_AI_TOOL_CALL_ID] = tool_call_id
        if extra_attributes:
            attributes.update(extra_attributes)

        span_name = f"gen_ai.{operation}"
        with self._tracer.start_as_current_span(span_name, attributes=attributes) as span:
            yield span

    def record_usage(
        self,
        span: Any,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """Attach token usage attributes to ``span``.

        Args:
            span: Span returned by :meth:`start_genai_span`.  ``None`` is
                tolerated (exporter disabled path).
            prompt_tokens: ``gen_ai.usage.prompt_tokens``.
            completion_tokens: ``gen_ai.usage.completion_tokens``.
        """
        if span is None:
            return
        if prompt_tokens is not None:
            span.set_attribute(ATTR_GEN_AI_USAGE_PROMPT_TOKENS, int(prompt_tokens))
        if completion_tokens is not None:
            span.set_attribute(ATTR_GEN_AI_USAGE_COMPLETION_TOKENS, int(completion_tokens))

    def shutdown(self) -> None:
        """Flush any in-flight spans and tear the provider down.

        Safe to call on a disabled exporter.
        """
        provider = self._provider
        if provider is None:
            return
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("OTLP exporter shutdown failed: %s", exc)

    # ----------------------------------------------------------- Internals

    def _init_tracer(self) -> bool:
        """Wire the OTLP span processor onto a tracer provider.

        Returns ``True`` when the tracer is ready and spans will be exported,
        ``False`` when the optional OTel exporter dep is missing or
        initialisation raises.  All failure paths log a single warning and
        leave the exporter in a disabled-but-callable state.
        """
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            logger.warning(
                "OTLP exporter disabled - install 'bernstein[otel]' for "
                "opentelemetry-sdk + opentelemetry-exporter-otlp-proto-grpc",
            )
            return False

        try:
            if self._provider is None:
                resource_attrs: dict[str, str] = {"service.name": self._config.service_name}
                resource_attrs.update(self._config.resource_attributes)
                self._provider = TracerProvider(resource=Resource.create(resource_attrs))
                # Register globally only when we created the provider ourselves.
                # When the caller supplied one (tests), they own the lifecycle.
                trace.set_tracer_provider(self._provider)

            # gRPC OTLP exporter is the optional extra.  When a tracer_provider
            # was supplied by the caller and endpoint is unset (in-memory test
            # path), skip the gRPC processor entirely.
            if self._config.endpoint is not None:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                span_exporter = OTLPSpanExporter(
                    endpoint=self._config.endpoint,
                    insecure=self._config.insecure,
                    headers=tuple(self._config.headers.items()) or None,
                )
                self._provider.add_span_processor(BatchSpanProcessor(span_exporter))

            self._tracer = trace.get_tracer(self._config.service_name, tracer_provider=self._provider)
        except ImportError:
            logger.warning(
                "OTLP gRPC exporter missing - install 'bernstein[otel]' to enable",
            )
            return False
        except Exception as exc:
            logger.warning("OTLP exporter init failed: %s", exc)
            return False

        return True


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


_default_exporter: GenAIOTLPExporter | None = None


def get_default_exporter() -> GenAIOTLPExporter:
    """Return the process-wide :class:`GenAIOTLPExporter`.

    Lazily constructed from the environment on first call.  Operators who
    need a custom config should instantiate :class:`GenAIOTLPExporter`
    directly instead of mutating the default.

    Returns:
        The shared exporter instance.  Always callable; may be a no-op when
        ``BERNSTEIN_OTEL_ENDPOINT`` is unset.
    """
    global _default_exporter
    if _default_exporter is None:
        _default_exporter = GenAIOTLPExporter()
    return _default_exporter


def reset_default_exporter() -> None:
    """Drop the cached default exporter (test helper)."""
    global _default_exporter
    if _default_exporter is not None:
        with contextlib.suppress(Exception):
            _default_exporter.shutdown()
    _default_exporter = None


__all__ = [
    "ATTR_GEN_AI_OPERATION_NAME",
    "ATTR_GEN_AI_REQUEST_MODEL",
    "ATTR_GEN_AI_SYSTEM",
    "ATTR_GEN_AI_TOOL_CALL_ID",
    "ATTR_GEN_AI_TOOL_NAME",
    "ATTR_GEN_AI_USAGE_COMPLETION_TOKENS",
    "ATTR_GEN_AI_USAGE_PROMPT_TOKENS",
    "DEFAULT_OPERATION",
    "DEFAULT_SERVICE_NAME",
    "OTEL_ENDPOINT_ENV",
    "OTEL_SERVICE_NAME_ENV",
    "GenAIOTLPExporter",
    "OTLPExporterConfig",
    "get_default_exporter",
    "reset_default_exporter",
]
