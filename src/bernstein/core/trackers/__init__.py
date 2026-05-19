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
from bernstein.core.trackers.servicenow import ServiceNowConfig, ServiceNowTracker

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
    "ServiceNowConfig",
    "ServiceNowTracker",
    "Status",
    "Ticket",
    "TrackerUnavailable",
    "TransitionResult",
]


def get_tracker(name: str, **kwargs: object) -> AbstractTrackerAdapter:
    """Construct a tracker adapter by short name.

    Minimal factory used by callers that resolve trackers from
    configuration. New adapters register here.

    Raises:
        ValueError: if ``name`` is not a known tracker.
    """
    if name == "servicenow":
        return ServiceNowTracker(**kwargs)  # type: ignore[arg-type]
    if name == "github_projects" or name == "github_projects_v2":
        from bernstein.core.trackers.builtin import (
            GitHubProjectsV2Adapter,
        )

        return GitHubProjectsV2Adapter(**kwargs)  # type: ignore[arg-type]
    msg = f"Unknown tracker '{name}'"
    raise ValueError(msg)
