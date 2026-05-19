"""Tracker adapter subsystem.

Trackers are external task sources (GitHub Projects v2, Jira, Linear, etc.).
Each adapter implements ``AbstractTrackerAdapter`` and produces normalised
``Ticket`` objects that flow into the orchestrator's task queue.
"""

from __future__ import annotations

from bernstein.core.trackers.contract import (
    AbstractTrackerAdapter,
    AttachResult,
    ClaimResult,
    Comment,
    CommentResult,
    IdempotencyConflict,
    OptimisticConcurrencyError,
    RateLimited,
    RoutingHint,
    Status,
    Ticket,
    TrackerUnavailable,
    TransitionResult,
)

__all__ = [
    "AbstractTrackerAdapter",
    "AttachResult",
    "ClaimResult",
    "Comment",
    "CommentResult",
    "IdempotencyConflict",
    "OptimisticConcurrencyError",
    "RateLimited",
    "RoutingHint",
    "Status",
    "Ticket",
    "TrackerUnavailable",
    "TransitionResult",
]
