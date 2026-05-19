"""Audit-trail emission for fork resolution events.

Operator-driven resolutions performed by ``bernstein lineage resolve``
emit a ``lineage.merge_entry`` lifecycle signal so downstream auditors
(JSONL trace, plugin hooks, dashboard) see the decision alongside the
existing per-write lineage trail.

The emission path is intentionally tolerant: if no hook infrastructure
is wired in (typical for unit tests and one-shot CLI invocations from a
fresh shell), the helper writes the payload to a JSONL audit file under
``.sdd/lineage/merge-audit.jsonl`` so the decision is still recoverable
later. Real plugin pipelines receive the same payload via the configured
emitter when one is registered with :func:`register_audit_emitter`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from bernstein.core.config.hook_events import HookEvent, LineageMergePayload

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DEFAULT_AUDIT_RELPATH",
    "LineageMergeAuditRecord",
    "build_audit_record",
    "emit_lineage_merge_entry",
    "register_audit_emitter",
    "reset_audit_emitter",
    "write_audit_record",
]

DEFAULT_AUDIT_RELPATH = Path("lineage") / "merge-audit.jsonl"
"""Path under ``.sdd/`` where merge-entry audit records land by default."""


@dataclass(frozen=True, slots=True)
class LineageMergeAuditRecord:
    """One JSONL row describing a fork resolution event."""

    event: str
    timestamp: float
    artefact_path: str
    policy: str
    winner_hash: str
    candidate_hashes: list[str] = field(default_factory=list[str])
    parent_hash: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event,
            "timestamp": self.timestamp,
            "artefact_path": self.artefact_path,
            "policy": self.policy,
            "winner_hash": self.winner_hash,
            "candidate_hashes": self.candidate_hashes.copy(),
            "parent_hash": self.parent_hash,
            "reason": self.reason,
        }


_emitter: Callable[[LineageMergePayload], None] | None = None


def register_audit_emitter(fn: Callable[[LineageMergePayload], None]) -> None:
    """Install a process-wide audit emitter.

    Set by the orchestrator at startup to forward merge audit events
    into the plugin hook pipeline. Tests and ad-hoc CLI runs leave this
    unset and rely on the JSONL fallback only.
    """
    global _emitter
    _emitter = fn


def reset_audit_emitter() -> None:
    """Clear any installed audit emitter; only used by tests."""
    global _emitter
    _emitter = None


def build_audit_record(payload: LineageMergePayload) -> LineageMergeAuditRecord:
    """Project a ``LineageMergePayload`` into its on-disk JSONL row form."""
    return LineageMergeAuditRecord(
        event=HookEvent.LINEAGE_MERGE_ENTRY.value,
        timestamp=payload.timestamp,
        artefact_path=payload.artefact_path,
        policy=payload.policy,
        winner_hash=payload.winner_hash,
        candidate_hashes=list(payload.candidate_hashes),
        parent_hash=payload.parent_hash,
        reason=payload.reason,
    )


def write_audit_record(record: LineageMergeAuditRecord, audit_path: Path) -> None:
    """Append ``record`` to ``audit_path`` as a single JSONL line."""
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")


def emit_lineage_merge_entry(
    *,
    artefact_path: str,
    policy: str,
    winner_hash: str,
    candidate_hashes: list[str],
    parent_hash: str,
    reason: str = "",
    sdd_dir: Path | None = None,
    now: float | None = None,
) -> LineageMergePayload:
    """Emit a ``lineage.merge_entry`` event.

    Writes a JSONL audit record under ``<sdd_dir>/lineage/merge-audit.jsonl``
    and, if a process-wide emitter is registered, forwards the payload
    through it as well. The fallback file write keeps the audit trail
    self-contained even when the orchestrator is not running.

    Returns the constructed :class:`LineageMergePayload` so callers can
    log or assert on it.
    """
    payload = LineageMergePayload(
        event=HookEvent.LINEAGE_MERGE_ENTRY,
        timestamp=now if now is not None else time.time(),
        artefact_path=artefact_path,
        policy=policy,
        winner_hash=winner_hash,
        candidate_hashes=candidate_hashes.copy(),
        parent_hash=parent_hash,
        reason=reason,
    )
    if sdd_dir is not None:
        audit_path = sdd_dir / DEFAULT_AUDIT_RELPATH
        write_audit_record(build_audit_record(payload), audit_path)
    if _emitter is not None:
        _emitter(payload)
    return payload
