"""Deterministic replay package for Bernstein agent runs.

This package provides the *gateway* that intercepts LLM requests and
tool dispatches so a previously recorded run can be re-executed against
recorded fixtures instead of live providers.

Public surface:

* :class:`ReplayGateway` — record/replay adapter around LLM + tool calls.
* :data:`RECORD_ENV_VAR` — env-var that opts-in to recording.
* :func:`diff_event_logs` — line-by-line first-divergence locator.

The existing ``RunRecorder`` in :mod:`bernstein.core.persistence.recorder`
already handles orchestrator-level lifecycle events. This package adds a
second, finer-grained log dedicated to LLM/tool I/O so replay can reproduce
adapter responses byte-for-byte.

The gateway is OFF by default. Set ``BERNSTEIN_RECORD=1`` or pass
``record=True`` explicitly to enable recording — we don't want to grow
``.sdd/`` on every casual user invocation.
"""

from __future__ import annotations

from bernstein.core.replay.diff import (
    DivergenceResult,
    diff_event_logs,
    load_events,
)
from bernstein.core.replay.gateway import (
    EVENTS_FILENAME,
    RECORD_ENV_VAR,
    GatewayMode,
    ReplayGateway,
    ReplayMissError,
    is_recording_enabled,
)

__all__ = [
    "EVENTS_FILENAME",
    "RECORD_ENV_VAR",
    "DivergenceResult",
    "GatewayMode",
    "ReplayGateway",
    "ReplayMissError",
    "diff_event_logs",
    "is_recording_enabled",
    "load_events",
]
