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
from bernstein.core.trackers.linear import LinearConfig, LinearTracker
from bernstein.core.trackers.registry import (
    DuplicateTrackerError,
    TrackerFactory,
    TrackerRegistration,
    TrackerRegistry,
    discover_plugin_trackers,
    get_registry,
    register_tracker,
)
from bernstein.core.trackers.servicenow import ServiceNowConfig, ServiceNowTracker

__all__ = [
    "AbstractTrackerAdapter",
    "AttachResult",
    "ClaimResult",
    "Comment",
    "CommentResult",
    "DuplicateTrackerError",
    "IdempotencyConflict",
    "LinearConfig",
    "LinearTracker",
    "OptimisticConcurrencyError",
    "RateLimited",
    "RoutingHint",
    "ServiceNowConfig",
    "ServiceNowTracker",
    "Status",
    "Ticket",
    "TrackerFactory",
    "TrackerRegistration",
    "TrackerRegistry",
    "TrackerUnavailable",
    "TransitionResult",
    "discover_plugin_trackers",
    "get_registry",
    "register_tracker",
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
    if name == "linear":
        return LinearTracker(**kwargs)  # type: ignore[arg-type]
    if name in ("github_projects", "github_projects_v2"):
        from bernstein.core.trackers.builtin import (
            GitHubProjectsV2Adapter,
        )

        return GitHubProjectsV2Adapter(**kwargs)  # type: ignore[arg-type]
    msg = f"Unknown tracker '{name}'"
    raise ValueError(msg)
