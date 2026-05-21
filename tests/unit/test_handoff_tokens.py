"""Unit tests for the handoff token store (op-005).

Coverage targets the ticket's three required scenarios:

* token TTL - expired tokens cannot be claimed and are swept on load,
* duplicate claim - a second claim raises ``HandoffClaimError``,
* missing session / unknown token - ``HandoffUnknownTokenError``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.handoff import (
    DEFAULT_TOKEN_TTL_S,
    HandoffClaimError,
    HandoffToken,
    HandoffTokenStore,
    HandoffUnknownTokenError,
    StreamTailBuffer,
    claim_token,
    emit_token,
)


class _StubClock:
    """Deterministic clock so we can drive TTL behaviour synchronously."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _store(tmp_path: Path, *, ttl_s: float = DEFAULT_TOKEN_TTL_S) -> tuple[HandoffTokenStore, _StubClock]:
    clock = _StubClock()
    return HandoffTokenStore(tmp_path, ttl_s=ttl_s, clock=clock), clock


def test_handoff_token_round_trip() -> None:
    """``HandoffToken`` survives a JSON round trip without losing fields."""
    token = HandoffToken(
        token="abc",
        session_id="sess-1",
        task_id="t-1",
        source_surface="terminal",
        issued_at=1_000.0,
        expires_at=1_300.0,
        note="thread:42",
    )
    decoded = HandoffToken.from_dict(json.loads(json.dumps(token.to_dict())))
    assert decoded == token


def test_issue_persists_token_to_disk(tmp_path: Path) -> None:
    """A freshly issued token is reachable via a second store instance."""
    issued = emit_token(
        tmp_path,
        session_id="sess-1",
        task_id="t-1",
        source_surface="terminal",
    )
    other = HandoffTokenStore(tmp_path)
    record = other.get(issued.token)
    assert record is not None
    assert record.session_id == "sess-1"
    assert record.task_id == "t-1"
    assert record.source_surface == "terminal"
    assert not record.claimed


def test_claim_marks_token_consumed(tmp_path: Path) -> None:
    """The first claim returns the payload and flips ``claimed``."""
    store, _ = _store(tmp_path)
    issued = store.issue(session_id="sess-1", source_surface="terminal")

    claimed = store.claim(issued.token, claimed_by="dashboard")
    assert claimed.session_id == "sess-1"
    assert claimed.claimed
    assert claimed.claimed_by == "dashboard"
    assert claimed.claimed_at is not None


def test_duplicate_claim_raises(tmp_path: Path) -> None:
    """Claiming an already-consumed token raises ``HandoffClaimError``."""
    store, _ = _store(tmp_path)
    issued = store.issue(session_id="sess-1")
    store.claim(issued.token, claimed_by="dashboard")

    with pytest.raises(HandoffClaimError):
        store.claim(issued.token, claimed_by="terminal")


def test_unknown_token_raises(tmp_path: Path) -> None:
    """An unissued token raises ``HandoffUnknownTokenError``."""
    store, _ = _store(tmp_path)
    with pytest.raises(HandoffUnknownTokenError):
        store.claim("nope-not-a-real-token", claimed_by="terminal")


def test_empty_token_raises_unknown(tmp_path: Path) -> None:
    """The empty string is treated as unknown, not as a claim error."""
    store, _ = _store(tmp_path)
    with pytest.raises(HandoffUnknownTokenError):
        store.claim("", claimed_by="terminal")


def test_expired_token_cannot_be_claimed(tmp_path: Path) -> None:
    """A token past TTL is purged on load and surfaces as unknown."""
    store, clock = _store(tmp_path, ttl_s=10.0)
    issued = store.issue(session_id="sess-1")

    clock.advance(11.0)  # past the 10s TTL

    with pytest.raises(HandoffUnknownTokenError):
        store.claim(issued.token, claimed_by="dashboard")
    # Sweep should have removed the record from the JSON file too.
    assert store.get(issued.token) is None


def test_session_id_required(tmp_path: Path) -> None:
    """Issuing without a session_id is rejected - protects against silent corruption."""
    store, _ = _store(tmp_path)
    with pytest.raises(ValueError):
        store.issue(session_id="")


def test_emit_and_claim_helpers_round_trip(tmp_path: Path) -> None:
    """The high-level helpers used by the CLI compose correctly."""
    issued = emit_token(
        tmp_path,
        session_id="sess-1",
        task_id="t-1",
        source_surface="terminal",
        ttl_s=60.0,
    )
    claimed = claim_token(tmp_path, issued.token, claimed_by="dashboard", ttl_s=60.0)
    assert claimed.session_id == "sess-1"
    assert claimed.claimed_by == "dashboard"


def test_all_returns_live_tokens_only(tmp_path: Path) -> None:
    """``all()`` filters out expired tokens and orders by issue time."""
    store, clock = _store(tmp_path, ttl_s=10.0)
    first = store.issue(session_id="sess-1")
    clock.advance(1.0)
    second = store.issue(session_id="sess-2")

    tokens = store.all()
    assert [t.token for t in tokens] == [first.token, second.token]

    clock.advance(15.0)  # both expire
    assert store.all() == []


def test_stream_tail_buffer_round_trip(tmp_path: Path) -> None:
    """The ring buffer captures and replays lines in order."""
    buffer = StreamTailBuffer(tmp_path, "sess-1", max_entries=4)
    for i in range(6):
        buffer.append(surface="terminal", text=f"line {i}", ts=1000.0 + i)
    entries = buffer.read()
    # Older lines are trimmed once we cross the cap.
    assert [e.text for e in entries] == ["line 2", "line 3", "line 4", "line 5"]
    assert all(e.surface == "terminal" for e in entries)


def test_stream_tail_buffer_limit(tmp_path: Path) -> None:
    """``read(limit=N)`` returns only the last N entries."""
    buffer = StreamTailBuffer(tmp_path, "sess-1")
    for i in range(5):
        buffer.append(surface="chat", text=f"line {i}")
    tail = buffer.read(limit=2)
    assert [e.text for e in tail] == ["line 3", "line 4"]


def test_stream_tail_buffer_session_id_required(tmp_path: Path) -> None:
    """An empty session_id is rejected to avoid colliding tail files."""
    with pytest.raises(ValueError):
        StreamTailBuffer(tmp_path, "")
