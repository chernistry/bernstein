"""Tracker adapter contract.

Defines the abstract base class, dataclasses, and exceptions that every
tracker adapter (GitHub Projects v2, Jira, Linear, GitLab, etc.) must
implement. The contract is intentionally minimal: ``pull_open_tickets``,
``add_comment``, ``transition`` are the only required hot-path methods.

The contract is deliberately HTTP-API-shaped: every method takes an
``idempotency_key`` where it makes sense, raises ``RateLimited`` with a
``retry_after`` hint, and returns dataclasses with stable field names so
the orchestrator can reason about results across tracker vendors.

This module is the smallest workable surface for the GitHub Projects v2
adapter (this ticket). The richer plugin-hookspec foundation lives in a
separate ticket; both can live side-by-side because the abstract base
exported here is a strict subset of that future surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingHint:
    """Optional routing metadata attached to a ticket.

    The orchestrator's routing layer consumes ``cli`` to pick a CLI adapter
    (e.g. ``claude``, ``codex``, ``aider``) for the ticket. ``role`` lets a
    tracker assign a specific Bernstein role prompt (e.g. ``qa``,
    ``security``). All fields are advisory; the router may override them.
    """

    cli: str | None = None
    role: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Ticket:
    """Normalised ticket representation.

    Attributes:
        id: Stable tracker-side identifier (e.g. project item node id).
        external_url: Browser URL for human follow-up.
        title: Short ticket title.
        body: Long-form description (may be empty).
        status: Current status as reported by the tracker.
        labels: List of label/tag strings (lowercase preferred).
        etag: Opaque revision token for optimistic concurrency control.
        routing_hint: Optional routing metadata.
        raw: Tracker-specific payload kept for adapters that need it.
        cost_cap_usd: Optional hard USD ceiling for the agent run that
            processes this ticket. ``None`` (default) keeps existing
            behaviour and lets the orchestrator-level budget apply. When
            set to a positive value the per-ticket cost cap enforcement
            in :mod:`bernstein.core.cost.ticket_cap` halts the agent at
            the next tool-call boundary once cumulative spend on the
            ticket would breach the cap. A value of ``0.0`` is treated
            as "halt immediately" (no work permitted) and is useful in
            tests/dry-runs.
    """

    id: str
    external_url: str
    title: str
    body: str
    status: str
    labels: tuple[str, ...] = ()
    etag: str | None = None
    routing_hint: RoutingHint = field(default_factory=RoutingHint)
    raw: dict[str, Any] = field(default_factory=dict)
    cost_cap_usd: float | None = None


@dataclass(frozen=True)
class Comment:
    """A comment posted on a ticket."""

    id: str
    body: str
    author: str | None = None


@dataclass(frozen=True)
class Status:
    """A status value supported by the tracker for a given ticket type."""

    id: str
    name: str


@dataclass(frozen=True)
class ClaimResult:
    """Result of claiming a ticket for an agent."""

    claimed: bool
    ticket_id: str
    agent_id: str
    etag: str | None = None


@dataclass(frozen=True)
class CommentResult:
    """Result of posting a comment."""

    comment_id: str
    ticket_id: str


@dataclass(frozen=True)
class TransitionResult:
    """Result of transitioning a ticket to a new status."""

    ticket_id: str
    new_status: str
    etag: str | None = None


@dataclass(frozen=True)
class AttachResult:
    """Result of attaching a blob to a ticket."""

    attachment_id: str
    ticket_id: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TrackerError(Exception):
    """Base class for all tracker adapter errors."""


class OptimisticConcurrencyError(TrackerError):
    """Raised when an ``etag`` precondition is not met."""


class IdempotencyConflict(TrackerError):
    """Raised when an idempotency key is reused with a conflicting payload."""


class RateLimited(TrackerError):
    """Raised when the tracker indicates the client must back off.

    Attributes:
        retry_after: Hint in seconds before the client should retry; may be
            ``None`` when the tracker did not provide a value.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TrackerUnavailable(TrackerError):
    """Raised when the tracker is unreachable or returned a 5xx."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class AbstractTrackerAdapter(ABC):
    """Base class for tracker adapters.

    Subclasses must implement at least ``pull_open_tickets``,
    ``add_comment``, and ``transition``. ``claim_ticket`` and
    ``attach_blob`` are optional; the default implementations raise
    ``NotImplementedError`` so adapters that do not support those
    operations can be explicit.
    """

    name: str = "abstract"

    @abstractmethod
    def pull_open_tickets(self, filter: dict[str, Any] | None = None) -> Iterator[Ticket]:
        """Yield open tickets that match ``filter``."""

    @abstractmethod
    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        """Post a comment on ``ticket_id``.

        Adapters that opt into the tracker-audit log (see
        :mod:`bernstein.core.lineage.tracker_audit`) are wrapped at the
        orchestrator boundary by
        :class:`~bernstein.core.lineage.tracker_audit.AuditingTrackerAdapter`,
        which brackets each call with a success or failure audit entry.
        The wrapper preserves the adapter's surface so concrete
        adapters can keep this signature unchanged.
        """

    @abstractmethod
    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        """Move ``ticket_id`` to ``status_id``.

        See :meth:`add_comment` for the audit-wrapping contract.
        """

    def claim_ticket(
        self,
        ticket_id: str,
        agent_id: str,
        *,
        etag: str | None = None,
    ) -> ClaimResult:
        """Claim ``ticket_id`` for ``agent_id`` (default: not supported)."""
        msg = f"{self.name} adapter does not support claim_ticket"
        raise NotImplementedError(msg)

    def attach_blob(
        self,
        ticket_id: str,
        blob: bytes,
        mime: str,
        *,
        idempotency_key: str | None = None,
    ) -> AttachResult:
        """Attach a binary blob to ``ticket_id`` (default: not supported).

        Subclasses override this to upload ``blob`` using the declared
        ``mime`` type. The default implementation discards the inputs so
        ``mime`` is referenced here to keep the signature documented and
        satisfy dead-code scanners.
        """
        del ticket_id, blob, mime, idempotency_key
        msg = f"{self.name} adapter does not support attach_blob"
        raise NotImplementedError(msg)
