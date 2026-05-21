"""Deterministic replay package for Bernstein agent runs.

This package provides the *gateway* that intercepts LLM requests and
tool dispatches so a previously recorded run can be re-executed against
recorded fixtures instead of live providers.

Public surface:

* :class:`ReplayGateway` - record/replay adapter around LLM + tool calls.
* :data:`RECORD_ENV_VAR` - env-var that opts-in to recording.
* :func:`diff_event_logs` - line-by-line first-divergence locator.

The existing ``RunRecorder`` in :mod:`bernstein.core.persistence.recorder`
already handles orchestrator-level lifecycle events. This package adds a
second, finer-grained log dedicated to LLM/tool I/O so replay can reproduce
adapter responses byte-for-byte.

The gateway is OFF by default. Set ``BERNSTEIN_RECORD=1`` or pass
``record=True`` explicitly to enable recording - we don't want to grow
``.sdd/`` on every casual user invocation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from pathlib import Path

    from bernstein.adapters.session_id import SessionIdRecord


def locate_run(sdd_dir: Path, conversation_id: str, adapter_name: str) -> SessionIdRecord | None:
    """Locate a previously recorded run by ``(conversation_id, adapter_name)``.

    Resolves the run directly from the deterministic session-id index under
    ``<sdd_dir>/session_index.json`` without scanning any ``events.jsonl``
    logs (AC #4 of deterministic session-id binding). Returns ``None`` when
    the pair was never recorded.
    """
    from bernstein.adapters.session_id import SessionIdIndex

    return SessionIdIndex(sdd_dir).lookup(conversation_id, adapter_name)


def record_run(sdd_dir: Path, conversation_id: str, adapter_name: str, run_id: str) -> SessionIdRecord:
    """Bind ``(conversation_id, adapter_name)`` to ``run_id`` for later replay.

    Writes the deterministic session-id index entry that :func:`locate_run`
    reads back. The latest binding for a key wins, so a rerun overwrites the
    prior slot rather than appending a duplicate.
    """
    from bernstein.adapters.session_id import SessionIdIndex

    return SessionIdIndex(sdd_dir).record(conversation_id, adapter_name, run_id)


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
    "locate_run",
    "record_run",
]
