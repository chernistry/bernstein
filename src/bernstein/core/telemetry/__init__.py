"""Opt-in operator observability for Bernstein.

This subpackage implements the activation-funnel measurement defined in
``.sdd/backlog/open/2026-05-17-feat-opt-in-first-run-telemetry.yaml``.

Top-line invariants (enforced by tests):

* Default state is off.  Nothing is emitted, no install id is generated.
* Precedence: ``DO_NOT_TRACK=1`` > ``BERNSTEIN_TELEMETRY`` env > config
  file > default-off.
* The install id is a UUID v4, persisted only after explicit opt-in.
* Every network failure is fail-closed; no exception ever bubbles out of
  the telemetry boundary into the caller.
* The on-disk queue at ``~/.bernstein/telemetry-queue.jsonl`` is the
  operator's audit record of every event their install has produced.

The public surface is intentionally small:

>>> from bernstein.core.telemetry import (
...     Client, TelemetryEvent, ErrorCategory, FirstRunCompletedPayload,
... )
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Back-compat re-exports.  The legacy ``bernstein.core.telemetry`` symbol
# space was an alias for ``bernstein.core.observability.telemetry`` served
# by the meta_path redirect in ``bernstein.core.__init__``.  Now that this
# directory is a real package the alias is shadowed; re-export the legacy
# surface here so existing call sites keep working unchanged.
# ---------------------------------------------------------------------------
from bernstein.core.observability.telemetry import (  # noqa: F401
    BUILTIN_PRESETS,
    ExporterPreset,
    _init_console_telemetry,  # pyright: ignore[reportPrivateUsage, reportUnusedImport]
    _init_http_telemetry,  # pyright: ignore[reportPrivateUsage, reportUnusedImport]
    get_meter,
    get_preset,
    get_tracer,
    init_telemetry,
    init_telemetry_from_preset,
    list_presets,
    start_span,
)
from bernstein.core.telemetry.client import (
    DEFAULT_ENDPOINT,
    ENDPOINT_ENV,
    Client,
    get_client,
    read_recent_events,
    reset_default_client,
)
from bernstein.core.telemetry.config import (
    OptInSource,
    OptInState,
    config_file_path,
    first_run_marker_path,
    install_id_path,
    is_enabled,
    is_first_run_acknowledged,
    mark_first_run_acknowledged,
    queue_path,
    resolve,
    write_enabled,
)
from bernstein.core.telemetry.events import (
    SCHEMA_VERSION,
    CommandInvokedPayload,
    DailyActivePayload,
    ErrorCategory,
    EventEnvelope,
    EventPayload,
    FirstRunCompletedPayload,
    FirstRunStartedPayload,
    InstallCompletedPayload,
    TelemetryEvent,
    build_envelope,
    serialize_event,
)
from bernstein.core.telemetry.install_id import ensure as ensure_install_id
from bernstein.core.telemetry.install_id import read as read_install_id
from bernstein.core.telemetry.install_id import reset as reset_install_id

__all__ = [
    "BUILTIN_PRESETS",
    "DEFAULT_ENDPOINT",
    "ENDPOINT_ENV",
    "SCHEMA_VERSION",
    "Client",
    "CommandInvokedPayload",
    "DailyActivePayload",
    "ErrorCategory",
    "EventEnvelope",
    "EventPayload",
    "ExporterPreset",
    "FirstRunCompletedPayload",
    "FirstRunStartedPayload",
    "InstallCompletedPayload",
    "OptInSource",
    "OptInState",
    "TelemetryEvent",
    "build_envelope",
    "config_file_path",
    "ensure_install_id",
    "first_run_marker_path",
    "get_client",
    "get_meter",
    "get_preset",
    "get_tracer",
    "init_telemetry",
    "init_telemetry_from_preset",
    "install_id_path",
    "is_enabled",
    "is_first_run_acknowledged",
    "list_presets",
    "mark_first_run_acknowledged",
    "queue_path",
    "read_install_id",
    "read_recent_events",
    "reset_default_client",
    "reset_install_id",
    "resolve",
    "serialize_event",
    "start_span",
    "write_enabled",
]
