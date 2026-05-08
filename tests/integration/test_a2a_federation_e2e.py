"""Cross-process A2A federation end-to-end tests.

Spins up two FastAPI servers on different ports — peer A (caller) and peer B
(callee) — and exercises the real HTTP path between them:

* happy-path delegation roundtrip with ledger consistency on both sides;
* peer state machine: ACTIVE → UNREACHABLE on connect failures, refresh on
  inbound traffic;
* retry/backoff on transient 5xx;
* terminal failure (port closed) → UNREACHABLE after retries exhausted;
* peer-side validation rejection (4xx) → ledger reflects REJECTED, peer
  remains ACTIVE;
* concurrent delegations from peer A → peer B all land, ledger consistent;
* invalid sender Agent Card → 400 at the boundary, no inbound bookkeeping;
* dedicated client injection — no httpx connection warnings on shutdown.

Skipped on Windows: the asyncio + uvicorn fixture combo is fragile under
the Windows ProactorEventLoop and the cluster-e2e/clm-fake-nim tests use
the same skip rationale.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest
import uvicorn
from bernstein.core.a2a import AgentCard
from bernstein.core.a2a_federation import (
    A2A_TASKS_PATH,
    A2ADelegationError,
    A2AFederation,
    A2ATaskRejectedError,
    FederatedTaskStatus,
    PeerState,
)
from fastapi import FastAPI

from bernstein.core.server import create_app

# Skip the whole module on Windows — uvicorn lifespan + asyncio fixtures
# wedge under ProactorEventLoop in CI. Same rationale as the cluster-e2e
# and clm-fake-nim integration suites that share this scaffolding.
pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="uvicorn + asyncio + httpx fixtures fragile on Windows CI runners",
)


# ---------------------------------------------------------------------------
# Helpers — port allocation and uvicorn lifecycle
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Return a port that is currently free on 127.0.0.1."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _PeerServer:
    """A FastAPI application running uvicorn in a background thread.

    Used as one side of the A2A federation — the test drives delegation
    from a separate ``A2AFederation`` instance that POSTs into this
    server's ``/a2a/v0/tasks`` endpoint over real HTTP.
    """

    def __init__(self, app: FastAPI, port: int) -> None:
        self.app = app
        self.port = port
        self.endpoint = f"http://127.0.0.1:{port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self, *, timeout: float = 5.0) -> None:
        """Start uvicorn in a daemon thread, block until ``server.started``."""
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            lifespan="off",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True, name=f"peer-{self.port}")
        thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not server.started:
            time.sleep(0.02)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=2)
            raise RuntimeError(f"peer server on port {self.port} did not start within {timeout}s")
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        """Signal uvicorn to exit and join the background thread."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def _build_peer_app(jsonl_path: Path) -> FastAPI:
    """Build a Bernstein FastAPI app rooted at ``jsonl_path``."""
    return create_app(jsonl_path=jsonl_path)


# ---------------------------------------------------------------------------
# Fixtures — peer A (caller) and peer B (callee), each in its own server
# ---------------------------------------------------------------------------


@pytest.fixture
def peer_a(tmp_path: Path) -> Generator[_PeerServer, None, None]:
    """Caller-side peer: server-app rooted at tmp_path/a/tasks.jsonl."""
    root = tmp_path / "a"
    root.mkdir()
    app = _build_peer_app(root / "tasks.jsonl")
    server = _PeerServer(app, _free_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def peer_b(tmp_path: Path) -> Generator[_PeerServer, None, None]:
    """Callee-side peer: server-app rooted at tmp_path/b/tasks.jsonl."""
    root = tmp_path / "b"
    root.mkdir()
    app = _build_peer_app(root / "tasks.jsonl")
    server = _PeerServer(app, _free_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def caller_card() -> AgentCard:
    """The Agent Card used by peer A to identify itself in delegations."""
    return AgentCard(
        name="peer-a",
        description="Test caller orchestrator",
        capabilities=["task_orchestration"],
        endpoint="http://127.0.0.1:0",
        provider="bernstein-test",
    )


@pytest.fixture
def caller_federation(peer_b: _PeerServer) -> A2AFederation:
    """Caller-side ``A2AFederation`` pre-registered with peer B."""
    fed = A2AFederation(local_endpoint="http://127.0.0.1:0")
    fed.register_peer("peer-b", peer_b.endpoint)
    return fed


# ---------------------------------------------------------------------------
# 1. Happy path — real HTTP roundtrip, ledger consistent on both sides
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_task_http_roundtrip(
    peer_a: _PeerServer,
    peer_b: _PeerServer,
    caller_federation: A2AFederation,
    caller_card: AgentCard,
) -> None:
    """End-to-end: peer A delegates → peer B accepts → both ledgers updated."""
    task = await caller_federation.delegate_task_http(
        "peer-b",
        "Render header SVG",
        sender_card=caller_card,
    )

    # Caller-side ledger.
    assert task.status == FederatedTaskStatus.SENT
    assert task.peer_name == "peer-b"
    assert task.remote_task_id  # non-empty
    peer = caller_federation.get_peer("peer-b")
    assert peer is not None
    assert peer.state == PeerState.ACTIVE

    # Callee-side ledger — pulled from the real FastAPI app state.
    callee_fed: A2AFederation = peer_b.app.state.a2a_federation  # type: ignore[attr-defined]
    inbound = callee_fed.list_tasks(direction="inbound")
    assert len(inbound) == 1
    assert inbound[0].status == FederatedTaskStatus.ACCEPTED
    assert inbound[0].remote_task_id == task.id
    assert inbound[0].peer_name == "peer-a"


# ---------------------------------------------------------------------------
# 2. Retry on transient 5xx, eventual success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_retries_on_5xx_then_succeeds(
    peer_b: _PeerServer,
    caller_federation: A2AFederation,
    caller_card: AgentCard,
) -> None:
    """Peer B returns 503 once, then 202 — retry path delivers."""
    # Patch the route to fail the first call, succeed afterwards.
    state = {"calls": 0}

    fed_b: A2AFederation = peer_b.app.state.a2a_federation  # type: ignore[attr-defined]
    real_accept = fed_b.accept_inbound_task

    def flaky_accept(*args: object, **kwargs: object) -> object:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("first attempt blows up")
        return real_accept(*args, **kwargs)  # type: ignore[arg-type]

    fed_b.accept_inbound_task = flaky_accept  # type: ignore[method-assign]

    try:
        task = await caller_federation.delegate_task_http(
            "peer-b",
            "Retried task",
            sender_card=caller_card,
            backoff_base=0.01,
        )
    finally:
        fed_b.accept_inbound_task = real_accept  # type: ignore[method-assign]

    assert task.status == FederatedTaskStatus.SENT
    assert state["calls"] >= 2
    inbound = fed_b.list_tasks(direction="inbound")
    assert len(inbound) == 1


# ---------------------------------------------------------------------------
# 3. All retries exhausted — peer marked UNREACHABLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_unreachable_marks_peer(
    caller_card: AgentCard,
) -> None:
    """Port closed before delegation → A2ADelegationError + peer UNREACHABLE."""
    fed = A2AFederation(local_endpoint="http://127.0.0.1:0")
    closed_port = _free_port()
    fed.register_peer("dead-peer", f"http://127.0.0.1:{closed_port}")

    with pytest.raises(A2ADelegationError) as exc_info:
        await fed.delegate_task_http(
            "dead-peer",
            "doomed",
            sender_card=caller_card,
            max_retries=2,
            backoff_base=0.01,
            connect_timeout=0.5,
            read_timeout=1.0,
        )

    assert exc_info.value.peer_name == "dead-peer"
    assert exc_info.value.attempts == 2

    peer = fed.get_peer("dead-peer")
    assert peer is not None
    assert peer.state == PeerState.UNREACHABLE

    # Outbound ledger entry is FAILED, not stuck in PENDING/SENT.
    outbound = fed.list_tasks(direction="outbound")
    assert len(outbound) == 1
    assert outbound[0].status == FederatedTaskStatus.FAILED


# ---------------------------------------------------------------------------
# 4. Peer rejects validation — REJECTED, peer stays ACTIVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_rejected_keeps_peer_active(
    peer_b: _PeerServer,
    caller_federation: A2AFederation,
    caller_card: AgentCard,
) -> None:
    """Peer rejects body (4xx) → ledger REJECTED, peer remains ACTIVE."""
    # Empty message triggers the 409 validation branch on the receiver.
    with pytest.raises(A2ATaskRejectedError) as exc_info:
        await caller_federation.delegate_task_http(
            "peer-b",
            "   ",  # whitespace-only message → server returns 409
            sender_card=caller_card,
            max_retries=3,
            backoff_base=0.01,
        )
    assert exc_info.value.peer_name == "peer-b"
    assert exc_info.value.status_code == 409

    peer = caller_federation.get_peer("peer-b")
    assert peer is not None
    assert peer.state == PeerState.ACTIVE  # rejected ≠ unreachable

    outbound = caller_federation.list_tasks(direction="outbound")
    assert len(outbound) == 1
    assert outbound[0].status == FederatedTaskStatus.REJECTED


# ---------------------------------------------------------------------------
# 5. Invalid sender card — peer rejects with 400, no ledger entry on B
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_sender_card_rejected_at_boundary(
    peer_b: _PeerServer,
) -> None:
    """Posting a malformed sender card returns 400; B's ledger stays empty."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{peer_b.endpoint}{A2A_TASKS_PATH}",
            json={
                "sender": {"description": "missing name"},  # name is required
                "task": {"id": "t1", "message": "Hi"},
            },
        )
    assert response.status_code == 400

    fed_b: A2AFederation = peer_b.app.state.a2a_federation  # type: ignore[attr-defined]
    assert fed_b.list_tasks() == []


# ---------------------------------------------------------------------------
# 6. Concurrent delegations from one caller to one callee — ledger consistent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_delegations_consistent_ledger(
    peer_b: _PeerServer,
    caller_federation: A2AFederation,
    caller_card: AgentCard,
) -> None:
    """Five parallel delegations all land; both ledgers see five tasks."""
    n = 5

    async def one_call(idx: int) -> str:
        task = await caller_federation.delegate_task_http(
            "peer-b",
            f"concurrent-task-{idx}",
            sender_card=caller_card,
        )
        return task.id

    ids = await asyncio.gather(*(one_call(i) for i in range(n)))
    assert len(set(ids)) == n  # all task ids unique

    outbound = caller_federation.list_tasks(direction="outbound")
    assert len(outbound) == n
    assert all(t.status == FederatedTaskStatus.SENT for t in outbound)

    fed_b: A2AFederation = peer_b.app.state.a2a_federation  # type: ignore[attr-defined]
    inbound = fed_b.list_tasks(direction="inbound")
    assert len(inbound) == n

    peer = caller_federation.get_peer("peer-b")
    assert peer is not None
    assert peer.task_count == n
    assert peer.state == PeerState.ACTIVE


# ---------------------------------------------------------------------------
# 7. Reusable httpx client — no un-closed connection warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_httpx_client_reused_no_leak(
    peer_b: _PeerServer,
    caller_federation: A2AFederation,
    caller_card: AgentCard,
) -> None:
    """Caller-supplied AsyncClient is reused across attempts and not closed."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i in range(3):
            task = await caller_federation.delegate_task_http(
                "peer-b",
                f"reuse-{i}",
                sender_card=caller_card,
                client=client,
            )
            assert task.status == FederatedTaskStatus.SENT
        # The caller still owns the client at this point: subsequent
        # call must succeed without ClientHasBeenClosed errors.
        await client.get(f"{peer_b.endpoint}/a2a/agent-card")


# ---------------------------------------------------------------------------
# 8. Bidirectional delegation — A→B then B→A round-trips both ledgers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bidirectional_delegation_two_servers(
    peer_a: _PeerServer,
    peer_b: _PeerServer,
    caller_card: AgentCard,
) -> None:
    """A delegates to B, then B delegates to A — both directions land."""
    # Caller-side feds for each peer's outbound calls.
    fed_a_out = A2AFederation(local_endpoint=peer_a.endpoint)
    fed_a_out.register_peer("peer-b", peer_b.endpoint)

    fed_b_out = A2AFederation(local_endpoint=peer_b.endpoint)
    fed_b_out.register_peer("peer-a", peer_a.endpoint)

    card_b = AgentCard(
        name="peer-b",
        description="Test B",
        endpoint=peer_b.endpoint,
        provider="bernstein-test",
    )

    # A → B
    task_ab = await fed_a_out.delegate_task_http("peer-b", "do thing in B", sender_card=caller_card)
    # B → A
    task_ba = await fed_b_out.delegate_task_http("peer-a", "do thing in A", sender_card=card_b)

    assert task_ab.status == FederatedTaskStatus.SENT
    assert task_ba.status == FederatedTaskStatus.SENT

    fed_a_in: A2AFederation = peer_a.app.state.a2a_federation  # type: ignore[attr-defined]
    fed_b_in: A2AFederation = peer_b.app.state.a2a_federation  # type: ignore[attr-defined]
    assert len(fed_a_in.list_tasks(direction="inbound")) == 1
    assert len(fed_b_in.list_tasks(direction="inbound")) == 1


# ---------------------------------------------------------------------------
# 9. Inbound idempotency — duplicate POSTs from a retry don't double-book
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_idempotent_on_duplicate_remote_task_id(
    peer_b: _PeerServer,
) -> None:
    """Bug fix: same ``(peer_name, remote_task_id)`` posted twice → one entry.

    Models the retry case: the receiver accepted the first POST but the
    202 reply got lost on the wire, so the caller retries; the receiver
    must dedupe rather than insert a second inbound row.
    """
    payload = {
        "sender": {
            "name": "duplicate-peer",
            "description": "Test caller",
            "endpoint": "http://example.invalid",
            "provider": "bernstein-test",
            "capabilities": [],
        },
        "task": {"id": "remote-stable-1", "message": "hello", "role": "backend"},
    }
    async with httpx.AsyncClient() as client:
        first = await client.post(f"{peer_b.endpoint}{A2A_TASKS_PATH}", json=payload)
        second = await client.post(f"{peer_b.endpoint}{A2A_TASKS_PATH}", json=payload)
    assert first.status_code == 202
    assert second.status_code == 202
    # Same local-id returned both times — proves the idempotent path.
    assert first.json()["id"] == second.json()["id"]
    fed_b: A2AFederation = peer_b.app.state.a2a_federation  # type: ignore[attr-defined]
    inbound = fed_b.list_tasks(direction="inbound", peer_name="duplicate-peer")
    assert len(inbound) == 1


# ---------------------------------------------------------------------------
# 10. Recovery — UNREACHABLE peer comes back, next delegate flips to ACTIVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_peer_recovers_to_active_on_success(
    peer_b: _PeerServer,
    caller_federation: A2AFederation,
    caller_card: AgentCard,
) -> None:
    """First delegation marks UNREACHABLE; a working call must reset to ACTIVE."""
    # Manually flip the peer to UNREACHABLE without touching the endpoint.
    peer = caller_federation.get_peer("peer-b")
    assert peer is not None
    peer.state = PeerState.UNREACHABLE

    # Real peer B is reachable, so the next delegation succeeds and the
    # ledger must transition the peer back to ACTIVE — proves the state
    # machine isn't stuck in UNREACHABLE forever.
    task = await caller_federation.delegate_task_http(
        "peer-b",
        "recovery",
        sender_card=caller_card,
    )
    assert task.status == FederatedTaskStatus.SENT
    assert peer.state == PeerState.ACTIVE
