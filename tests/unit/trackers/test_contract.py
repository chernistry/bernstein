"""Contract tests for ``AbstractTrackerAdapter``.

Every concrete tracker adapter is expected to behave identically on the
core operations (claim/comment/transition/attach) when given the same
sequence of calls. These tests use :class:`InMemoryTracker` as the
canonical reference implementation and document the contract behaviour
that real adapters must mirror.

The four required behaviours, per the ticket's acceptance criteria:

1. ``claim_ticket`` raises ``OptimisticConcurrencyError`` on a stale
   etag.
2. ``add_comment`` with a duplicate idempotency key + identical body
   returns the original ``CommentResult``; with a different body it
   raises ``IdempotencyConflict``.
3. ``transition`` raises ``OptimisticConcurrencyError`` on a stale
   etag.
4. ``RateLimited`` propagates with a ``retry_after`` hint.
"""

from __future__ import annotations

import pytest

from bernstein.core.trackers.contract import (
    IdempotencyConflict,
    OptimisticConcurrencyError,
    RateLimited,
    TrackerUnavailable,
)
from tests.fixtures.trackers.in_memory_tracker import InMemoryTracker


@pytest.fixture
def tracker() -> InMemoryTracker:
    """A fresh in-memory tracker with a single seeded open ticket."""
    fake = InMemoryTracker()
    fake.seed("Investigate flaky test", body="Reproduces on Python 3.13.", labels=("flaky",))
    return fake


# ---------------------------------------------------------------------------
# pull_open_tickets
# ---------------------------------------------------------------------------


def test_pull_open_tickets_yields_open_only(tracker: InMemoryTracker) -> None:
    tracker.seed("Closed ticket", status="closed")
    open_tickets = list(tracker.pull_open_tickets())
    assert [t.title for t in open_tickets] == ["Investigate flaky test"]


def test_pull_open_tickets_honours_status_filter(tracker: InMemoryTracker) -> None:
    tracker.seed("In review", status="in_review")
    tickets = list(tracker.pull_open_tickets({"status": "in_review"}))
    assert [t.status for t in tickets] == ["in_review"]


def test_pull_open_tickets_exposes_etag(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    assert ticket.etag is not None
    assert ticket.id == "T-1"


# ---------------------------------------------------------------------------
# claim_ticket
# ---------------------------------------------------------------------------


def test_claim_ticket_succeeds_with_correct_etag(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    result = tracker.claim_ticket(ticket.id, agent_id="agent-1", etag=ticket.etag)
    assert result.claimed is True
    assert result.agent_id == "agent-1"
    assert result.etag != ticket.etag


def test_claim_ticket_raises_on_stale_etag(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    # Bump the etag with an unrelated transition.
    tracker.transition(ticket.id, "in_progress", etag=ticket.etag)
    with pytest.raises(OptimisticConcurrencyError):
        tracker.claim_ticket(ticket.id, agent_id="agent-1", etag=ticket.etag)


def test_claim_ticket_refuses_second_claimant(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    first = tracker.claim_ticket(ticket.id, agent_id="agent-1", etag=ticket.etag)
    second = tracker.claim_ticket(ticket.id, agent_id="agent-2", etag=first.etag)
    assert first.claimed is True
    assert second.claimed is False


# ---------------------------------------------------------------------------
# add_comment / idempotency
# ---------------------------------------------------------------------------


def test_add_comment_idempotency_replay_returns_same_result(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    first = tracker.add_comment(ticket.id, "Spawning agent.", idempotency_key="k-1")
    second = tracker.add_comment(ticket.id, "Spawning agent.", idempotency_key="k-1")
    assert first.comment_id == second.comment_id


def test_add_comment_idempotency_conflict_on_different_body(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    tracker.add_comment(ticket.id, "Spawning agent.", idempotency_key="k-1")
    with pytest.raises(IdempotencyConflict):
        tracker.add_comment(ticket.id, "Different body.", idempotency_key="k-1")


def test_add_comment_without_key_creates_distinct_comments(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    first = tracker.add_comment(ticket.id, "One.")
    second = tracker.add_comment(ticket.id, "Two.")
    assert first.comment_id != second.comment_id


# ---------------------------------------------------------------------------
# transition / concurrency conflict
# ---------------------------------------------------------------------------


def test_transition_raises_on_stale_etag(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    fresh = tracker.transition(ticket.id, "in_progress", etag=ticket.etag)
    with pytest.raises(OptimisticConcurrencyError):
        tracker.transition(ticket.id, "done", etag=ticket.etag)
    assert fresh.new_status == "in_progress"


def test_transition_idempotency_replay(tracker: InMemoryTracker) -> None:
    [ticket] = list(tracker.pull_open_tickets())
    first = tracker.transition(ticket.id, "done", etag=ticket.etag, idempotency_key="t-1")
    second = tracker.transition(ticket.id, "done", etag=first.etag, idempotency_key="t-1")
    assert first.etag == second.etag


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------


def test_rate_limited_propagates_retry_after() -> None:
    tracker = InMemoryTracker(rate_limit_after=2, retry_after=12.5)
    tracker.seed("First")
    # Two successful calls.
    list(tracker.pull_open_tickets())
    list(tracker.pull_open_tickets())
    # Third raises RateLimited.
    with pytest.raises(RateLimited) as excinfo:
        list(tracker.pull_open_tickets())
    assert excinfo.value.retry_after == 12.5


# ---------------------------------------------------------------------------
# tracker unavailable
# ---------------------------------------------------------------------------


def test_tracker_unavailable_raises_on_every_op(tracker: InMemoryTracker) -> None:
    tracker.set_unavailable(True)
    with pytest.raises(TrackerUnavailable):
        list(tracker.pull_open_tickets())
    with pytest.raises(TrackerUnavailable):
        tracker.add_comment("T-1", "won't land")
