"""HMAC-chained audit events for cluster operations.

Thin wrapper over the global :class:`bernstein.core.security.audit.AuditLog`
singleton so cluster code paths can record node lifecycle, task stealing
and autoscaling decisions through the existing tamper-evident chain.

When no audit log is wired (e.g. during unit tests that don't bootstrap
the lifecycle module), every helper is a silent no-op.  This keeps the
cluster module importable in isolation and avoids forcing every test
fixture to spin up an HMAC key.
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event-type constants - kept as a closed StrEnum-style block so callers
# import names instead of stringly-typing.  String values match the
# regulatory-lineage / lethal-trifecta convention (UPPER_SNAKE).
# ---------------------------------------------------------------------------

EVENT_NODE_REGISTERED: Final[str] = "CLUSTER_NODE_REGISTERED"
EVENT_NODE_LEFT: Final[str] = "CLUSTER_NODE_LEFT"
EVENT_NODE_CORDONED: Final[str] = "CLUSTER_NODE_CORDONED"
EVENT_NODE_DRAINED: Final[str] = "CLUSTER_NODE_DRAINED"
EVENT_TASK_STOLEN: Final[str] = "CLUSTER_TASK_STOLEN"
EVENT_SCALE_DECISION: Final[str] = "CLUSTER_SCALE_DECISION"

_RESOURCE_NODE: Final[str] = "cluster_node"
_RESOURCE_TASK: Final[str] = "cluster_task"
_RESOURCE_SCALE: Final[str] = "cluster_scale"

# Reasons for CLUSTER_NODE_LEFT.  Closed set - anything else is bucketed
# under "unknown" before the audit entry is written.
_KNOWN_LEAVE_REASONS: Final[frozenset[str]] = frozenset(
    {"graceful", "timeout", "unregistered"},
)


def _audit_log() -> object | None:
    """Return the wired AuditLog singleton, or None.

    Imported lazily to break a circular import (lifecycle -> task_store
    -> cluster).
    """
    try:
        from bernstein.core.tasks.lifecycle import get_audit_log
    except ImportError:  # pragma: no cover - lifecycle always present in prod
        return None
    try:
        return get_audit_log()
    except Exception:  # pragma: no cover - defensive
        logger.debug("get_audit_log raised", exc_info=True)
        return None


def _safe_log(
    event_type: str,
    actor: str,
    resource_type: str,
    resource_id: str,
    details: dict[str, object],
) -> None:
    """Append one event through the wired audit log; swallow errors.

    The cluster control plane must never crash because the audit sink is
    misconfigured - we log at debug and move on.
    """
    log = _audit_log()
    if log is None:
        return
    try:
        log.log(event_type, actor, resource_type, resource_id, details)
    except Exception:
        logger.debug("Failed to record cluster audit event %s", event_type, exc_info=True)


def record_node_registered(
    node_id: str,
    *,
    role: str,
    registered_at: float,
    initial_capacity: int,
) -> None:
    """Record CLUSTER_NODE_REGISTERED."""
    _safe_log(
        EVENT_NODE_REGISTERED,
        actor="cluster.registry",
        resource_type=_RESOURCE_NODE,
        resource_id=node_id,
        details={
            "node_id": node_id,
            "role": role,
            "registered_at": registered_at,
            "initial_capacity": initial_capacity,
        },
    )


def record_node_left(node_id: str, *, reason: str) -> None:
    """Record CLUSTER_NODE_LEFT.

    *reason* is bucketed against the closed set graceful / timeout /
    unregistered.  Anything else is normalised to ``"unknown"``.
    """
    bucket = (reason).strip().lower()
    if bucket not in _KNOWN_LEAVE_REASONS:
        bucket = "unknown"
    _safe_log(
        EVENT_NODE_LEFT,
        actor="cluster.registry",
        resource_type=_RESOURCE_NODE,
        resource_id=node_id,
        details={"node_id": node_id, "reason": bucket},
    )


def record_node_cordoned(node_id: str) -> None:
    """Record CLUSTER_NODE_CORDONED."""
    _safe_log(
        EVENT_NODE_CORDONED,
        actor="cluster.registry",
        resource_type=_RESOURCE_NODE,
        resource_id=node_id,
        details={"node_id": node_id},
    )


def record_node_drained(node_id: str) -> None:
    """Record CLUSTER_NODE_DRAINED."""
    _safe_log(
        EVENT_NODE_DRAINED,
        actor="cluster.registry",
        resource_type=_RESOURCE_NODE,
        resource_id=node_id,
        details={"node_id": node_id},
    )


def record_task_stolen(
    task_id: str,
    *,
    from_node: str,
    to_node: str,
    queue_depth_delta: int,
) -> None:
    """Record CLUSTER_TASK_STOLEN."""
    _safe_log(
        EVENT_TASK_STOLEN,
        actor="cluster.task_stealing",
        resource_type=_RESOURCE_TASK,
        resource_id=task_id,
        details={
            "task_id": task_id,
            "from_node": from_node,
            "to_node": to_node,
            "queue_depth_delta": queue_depth_delta,
        },
    )


def record_scale_decision(
    *,
    action: str,
    target_count: int,
    backend: str,
    dry_run: bool,
) -> None:
    """Record CLUSTER_SCALE_DECISION."""
    _safe_log(
        EVENT_SCALE_DECISION,
        actor="cluster.autoscaler",
        resource_type=_RESOURCE_SCALE,
        resource_id=backend,
        details={
            "action": action,
            "target_count": target_count,
            "backend": backend,
            "dry_run": dry_run,
        },
    )


__all__ = [
    "EVENT_NODE_CORDONED",
    "EVENT_NODE_DRAINED",
    "EVENT_NODE_LEFT",
    "EVENT_NODE_REGISTERED",
    "EVENT_SCALE_DECISION",
    "EVENT_TASK_STOLEN",
    "record_node_cordoned",
    "record_node_drained",
    "record_node_left",
    "record_node_registered",
    "record_scale_decision",
    "record_task_stolen",
]
