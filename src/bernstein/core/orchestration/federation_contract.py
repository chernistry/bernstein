"""Minimum tracker contract consumed by the federation layer.

This module exposes a narrow, federation-shaped view of a ticket and a
``TrackerAdapter`` protocol the federation builder and dispatcher are
written against. It deliberately does NOT replace
``bernstein.core.trackers.contract``, which is the canonical
adapter-implementation contract (``AbstractTrackerAdapter``,
``CommentResult``, idempotency keys, etag preconditions, ...) consumed
by concrete adapters such as the GitHub Projects v2 adapter.

Why two surfaces side by side
-----------------------------
The federation layer reasons about a normalised cross-tracker graph and
needs fields the canonical contract intentionally omits at the
adapter-implementation boundary - in particular per-ticket
``comments`` and free-form ``custom_fields`` that the link detectors
scan. A concrete adapter that wants to participate in federation builds
this view on top of its canonical contract; the federation layer never
imports from ``bernstein.core.trackers`` directly so the two contracts
can evolve independently.

The dedicated ``feat/tracker-plugin-hookspec`` ticket will reconcile
these surfaces; until then the federation layer ships against the
minimum shape below so its tests and audit log can land in parallel
with concrete adapter work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "Comment",
    "Ticket",
    "TrackerAdapter",
    "TrackerError",
    "TrackerReadOnlyError",
    "TrackerUnknownError",
]


class TrackerError(Exception):
    """Base class for federation-shaped tracker errors."""


class TrackerReadOnlyError(TrackerError):
    """Raised when a write call hits a read-only adapter or scope."""


class TrackerUnknownError(TrackerError):
    """Raised when an adapter cannot find a referenced ticket."""


@dataclass(frozen=True, slots=True)
class Comment:
    """A single comment attached to a ticket.

    Attributes
    ----------
    author:
        Stable identifier of the comment author within the source
        tracker. Format is provider-specific.
    body:
        Plain-text or lightly-marked-up comment body. Adapters strip
        provider-private control characters before returning.
    custom_fields:
        Optional bag of provider-specific extras (e.g. Linear reactions,
        Jira visibility). The federation layer ignores unknown keys.
    """

    author: str
    body: str
    custom_fields: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class Ticket:
    """A normalised ticket view used by the federation layer.

    The federation layer consumes only the fields below. Adapters may
    populate ``custom_fields`` with provider-specific data the custom
    field link detector matches against.
    """

    tracker: str
    ticket_id: str
    title: str
    body: str
    comments: tuple[Comment, ...] = ()
    custom_fields: dict[str, str] = field(default_factory=dict[str, str])
    url: str = ""


@runtime_checkable
class TrackerAdapter(Protocol):
    """The minimum surface a tracker adapter must expose to federation.

    Concrete adapters (e.g. ``GitHubProjectsV2Adapter``) typically
    inherit from ``bernstein.core.trackers.contract.AbstractTrackerAdapter``
    for their HTTP-API-shaped contract and additionally implement the
    protocol below for federation participation.
    """

    name: str
    tracker_uri_base: str

    def list_tickets(self) -> Iterable[Ticket]:  # pragma: no cover - protocol
        """Return the open tickets visible to the federation builder."""
        ...

    def get_ticket(self, ticket_id: str) -> Ticket:  # pragma: no cover - protocol
        """Fetch a single ticket by id; raise ``TrackerUnknownError`` if missing."""
        ...

    def fetch_ticket(self, ticket_id: str) -> Ticket:  # pragma: no cover - protocol
        """Fetch a single ticket by id; legacy federation dispatcher spelling."""
        ...

    def add_comment(self, ticket_id: str, body: str) -> None:  # pragma: no cover - protocol
        """Post a comment; raise ``TrackerReadOnlyError`` if not writable."""
        ...

    def transition(self, ticket_id: str, state: str) -> None:  # pragma: no cover - protocol
        """Transition the ticket to ``state``; raise ``TrackerReadOnlyError`` if not writable."""
        ...
