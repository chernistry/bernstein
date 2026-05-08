"""MCP-009: A2A protocol federation.

Exchange tasks with external orchestrators via A2A protocol. Builds on
the existing :mod:`bernstein.core.a2a` module to add:

- Peer registry for known external orchestrators.
- Outbound task delegation (send a task to an external peer over HTTP).
- Inbound task acceptance (receive and track federated tasks).
- Status synchronisation between local and remote task states.

Wire transport
--------------
``delegate_task_http`` performs a real HTTP POST to the peer's
``/a2a/v0/tasks`` endpoint, with retry on 5xx and connection errors,
and updates the local ledger transactionally:

* on success → ``mark_sent`` is called and the peer's ``last_seen`` /
  ``state`` are refreshed to ``ACTIVE``;
* on final failure → the federated task is moved to ``FAILED`` and the
  peer transitions to ``UNREACHABLE``; an :class:`A2ADelegationError`
  is raised so the caller can react.

The in-memory ledger is still authoritative for local state — HTTP is
only the wire transport. Synchronous :meth:`delegate_task` is preserved
for bookkeeping-only use cases (tests, dry runs, planning) and remains
the path used by all 25 existing unit tests.

Usage::

    from bernstein.core.protocols.a2a_federation import A2AFederation

    fed = A2AFederation(local_endpoint="http://orchestrator:8052")
    fed.register_peer("design-team", "http://design.local:8052")
    task = await fed.delegate_task_http(
        "design-team",
        "Create wireframes for login page",
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from bernstein.core.protocols.a2a.a2a import AgentCard

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults & constants
# ---------------------------------------------------------------------------

# Path on the peer where federated tasks are POSTed.
A2A_TASKS_PATH = "/a2a/v0/tasks"

# Default httpx timeouts: 10s connect / 30s read.
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0

# Retry policy for delegate_task_http.
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.25  # seconds — 0.25, 0.5, 1.0 by default


class A2ADelegationError(RuntimeError):
    """Raised when a task delegation fails after all retries.

    Attributes:
        peer_name: Peer that the delegation targeted.
        attempts: Number of attempts made (including the first).
        last_error: The last underlying error (httpx exception, status code).
    """

    def __init__(
        self,
        peer_name: str,
        attempts: int,
        last_error: str,
    ) -> None:
        super().__init__(f"A2A delegation to peer '{peer_name}' failed after {attempts} attempts: {last_error}")
        self.peer_name = peer_name
        self.attempts = attempts
        self.last_error = last_error


class A2ATaskRejectedError(RuntimeError):
    """Raised when the peer accepts the request but rejects the task body.

    Distinct from :class:`A2ADelegationError`: rejection means the peer is
    reachable and authoritative — there is nothing to retry. The local
    ledger reflects ``REJECTED`` for the task and the peer remains
    ``ACTIVE``.
    """

    def __init__(self, peer_name: str, status_code: int, detail: str) -> None:
        super().__init__(f"Peer '{peer_name}' rejected task (HTTP {status_code}): {detail}")
        self.peer_name = peer_name
        self.status_code = status_code
        self.detail = detail


class PeerState(StrEnum):
    """Connection state for a federation peer."""

    ACTIVE = "active"
    UNREACHABLE = "unreachable"
    DEREGISTERED = "deregistered"


@dataclass
class FederationPeer:
    """A known external A2A-compatible orchestrator.

    Attributes:
        name: Human-readable peer name.
        endpoint: Base URL of the peer's A2A endpoint.
        state: Current connection state.
        capabilities: Capability tags the peer advertises.
        last_seen: Unix timestamp of last successful communication.
        task_count: Number of tasks delegated to this peer.
    """

    name: str
    endpoint: str
    state: PeerState = PeerState.ACTIVE
    capabilities: list[str] = field(default_factory=list)  # type: ignore[reportUnknownVariableType]
    last_seen: float = field(default_factory=time.time)
    task_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "name": self.name,
            "endpoint": self.endpoint,
            "state": self.state.value,
            "capabilities": list(self.capabilities),
            "last_seen": self.last_seen,
            "task_count": self.task_count,
        }


class FederatedTaskStatus(StrEnum):
    """Status of a federated (delegated) task."""

    PENDING = "pending"
    SENT = "sent"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class FederatedTask:
    """A task delegated to or received from a federation peer.

    Attributes:
        id: Local federation task ID.
        peer_name: Name of the remote peer.
        remote_task_id: Task ID on the remote peer (set after delegation).
        local_task_id: Corresponding local Bernstein task ID, if any.
        message: Task description.
        role: Role hint for task routing.
        direction: "outbound" (we delegated) or "inbound" (peer delegated to us).
        status: Current federation status.
        created_at: Unix timestamp.
        updated_at: Unix timestamp of last update.
        result: Result data from the remote peer.
    """

    id: str
    peer_name: str
    message: str
    role: str = "backend"
    remote_task_id: str = ""
    local_task_id: str = ""
    direction: str = "outbound"
    status: FederatedTaskStatus = FederatedTaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "peer_name": self.peer_name,
            "remote_task_id": self.remote_task_id,
            "local_task_id": self.local_task_id,
            "message": self.message,
            "role": self.role,
            "direction": self.direction,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class A2AFederation:
    """A2A protocol federation for exchanging tasks with external orchestrators.

    Args:
        local_endpoint: This orchestrator's A2A endpoint URL.
    """

    def __init__(self, local_endpoint: str = "http://localhost:8052") -> None:
        self._local_endpoint = local_endpoint
        self._peers: dict[str, FederationPeer] = {}
        self._tasks: dict[str, FederatedTask] = {}
        self._by_peer: dict[str, list[str]] = {}
        # Per-peer asyncio lock keeps ledger writes serialised within the
        # same peer while still allowing cross-peer concurrency.
        self._peer_locks: dict[str, asyncio.Lock] = {}

    def register_peer(
        self,
        name: str,
        endpoint: str,
        capabilities: list[str] | None = None,
    ) -> FederationPeer:
        """Register a federation peer.

        Args:
            name: Peer name.
            endpoint: Peer's A2A endpoint URL.
            capabilities: Optional capability tags.

        Returns:
            The registered peer.
        """
        peer = FederationPeer(
            name=name,
            endpoint=endpoint.rstrip("/"),
            capabilities=list(capabilities) if capabilities else [],
        )
        self._peers[name] = peer
        self._by_peer.setdefault(name, [])
        logger.info("Registered federation peer '%s' at %s", name, endpoint)
        return peer

    def deregister_peer(self, name: str) -> bool:
        """Deregister a federation peer.

        Args:
            name: Peer name.

        Returns:
            True if the peer was found and deregistered.
        """
        peer = self._peers.get(name)
        if peer is None:
            return False
        peer.state = PeerState.DEREGISTERED
        logger.info("Deregistered federation peer '%s'", name)
        return True

    def get_peer(self, name: str) -> FederationPeer | None:
        """Look up a peer by name."""
        return self._peers.get(name)

    def list_peers(self) -> list[FederationPeer]:
        """Return all registered peers."""
        return list(self._peers.values())

    def delegate_task(
        self,
        peer_name: str,
        message: str,
        role: str = "backend",
    ) -> FederatedTask | None:
        """Create a pending outbound delegation entry in the local ledger.

        This is the synchronous bookkeeping primitive: it records intent
        but performs no I/O. For the wire-level path (HTTP POST + retry +
        peer-state lifecycle), use :meth:`delegate_task_http`.

        Args:
            peer_name: Name of the target peer.
            message: Task description.
            role: Role hint for routing.

        Returns:
            The created FederatedTask, or ``None`` if the peer is unknown
            or has been deregistered.
        """
        peer = self._peers.get(peer_name)
        if peer is None or peer.state == PeerState.DEREGISTERED:
            logger.warning("Cannot delegate to peer '%s': not available", peer_name)
            return None

        task = FederatedTask(
            id=uuid.uuid4().hex[:12],
            peer_name=peer_name,
            message=message,
            role=role,
            direction="outbound",
        )
        self._tasks[task.id] = task
        self._by_peer.setdefault(peer_name, []).append(task.id)
        peer.task_count += 1
        logger.info("Delegated task '%s' to peer '%s'", task.id, peer_name)
        return task

    async def delegate_task_http(
        self,
        peer_name: str,
        message: str,
        role: str = "backend",
        *,
        sender_card: AgentCard | dict[str, Any] | None = None,
        client: httpx.AsyncClient | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> FederatedTask:
        """Delegate a task to a peer over HTTP, with retries and ledger updates.

        Performs a POST to ``{peer.endpoint}/a2a/v0/tasks`` carrying the
        sender Agent Card and task body. On success the local ledger is
        updated via :meth:`mark_sent` and the peer is refreshed to
        ``ACTIVE``. On final failure the peer is marked ``UNREACHABLE``
        and :class:`A2ADelegationError` is raised. If the peer responds
        with a non-retryable 4xx (anything other than 408/425/429), the
        task is moved to ``REJECTED`` and :class:`A2ATaskRejectedError`
        is raised without further retries.

        Args:
            peer_name: Name of the target peer (must be registered).
            message: Task description.
            role: Role hint for routing.
            sender_card: Optional :class:`AgentCard` describing this
                orchestrator. Falls back to a minimal card derived from
                ``local_endpoint``.
            client: Optional preconfigured ``httpx.AsyncClient`` (test
                injection or shared connection pooling). When omitted a
                client is created and closed within the call.
            max_retries: Total attempts on transient errors (5xx,
                connect/read errors). Default 3.
            backoff_base: Exponential backoff base in seconds. Sleep on
                attempt ``n`` is ``backoff_base * 2**(n-1)``.
            connect_timeout: HTTP connect timeout per attempt.
            read_timeout: HTTP read timeout per attempt.

        Returns:
            The updated FederatedTask in ``SENT`` state.

        Raises:
            A2ADelegationError: All retries exhausted; peer marked UNREACHABLE.
            A2ATaskRejectedError: Peer responded with a non-retriable 4xx.
            ValueError: Peer is unknown or has been deregistered.
        """
        peer = self._peers.get(peer_name)
        if peer is None or peer.state == PeerState.DEREGISTERED:
            raise ValueError(f"Cannot delegate to peer '{peer_name}': not registered or deregistered")

        task = self.delegate_task(peer_name, message, role)
        if task is None:  # pragma: no cover — guarded above
            raise ValueError(f"Failed to allocate ledger entry for peer '{peer_name}'")

        # Lock here so concurrent in-flight delegations to the same peer
        # don't interleave UNREACHABLE/ACTIVE writes; cross-peer
        # delegations remain concurrent.
        lock = self._peer_locks.setdefault(peer_name, asyncio.Lock())
        async with lock:
            try:
                remote_task_id = await self._post_with_retries(
                    peer=peer,
                    task=task,
                    sender_card=sender_card,
                    client=client,
                    max_retries=max_retries,
                    backoff_base=backoff_base,
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                )
            except A2ATaskRejectedError:
                # Peer reached us; ledger reflects rejection but peer remains ACTIVE.
                task.status = FederatedTaskStatus.REJECTED
                task.updated_at = time.time()
                raise
            except A2ADelegationError:
                # Final failure: mark task FAILED and peer UNREACHABLE.
                task.status = FederatedTaskStatus.FAILED
                task.updated_at = time.time()
                peer.state = PeerState.UNREACHABLE
                raise
            except BaseException:
                # Bug fix: anything unexpected (asyncio.CancelledError,
                # programming errors) used to leave the ledger entry in
                # PENDING forever. Mark FAILED and re-raise so the caller
                # has a clean ledger view to recover from.
                task.status = FederatedTaskStatus.FAILED
                task.updated_at = time.time()
                raise

            # Success path.
            self.mark_sent(task.id, remote_task_id)
            peer.state = PeerState.ACTIVE
            peer.last_seen = time.time()
            return task

    async def _post_with_retries(
        self,
        *,
        peer: FederationPeer,
        task: FederatedTask,
        sender_card: AgentCard | dict[str, Any] | None,
        client: httpx.AsyncClient | None,
        max_retries: int,
        backoff_base: float,
        connect_timeout: float,
        read_timeout: float,
    ) -> str:
        """POST the task to the peer with retry-on-5xx/connect-error.

        Returns:
            The ``remote_task_id`` assigned by the peer.

        Raises:
            A2ATaskRejectedError: 4xx (non-retryable).
            A2ADelegationError: All retries exhausted.
        """
        url = f"{peer.endpoint}{A2A_TASKS_PATH}"
        payload = self._build_payload(
            task=task,
            sender_card=sender_card,
            local_endpoint=self._local_endpoint,
        )

        owns_client = client is None
        timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        ac = client or httpx.AsyncClient(timeout=timeout)
        attempts = 0
        last_error = "unknown error"
        try:
            for attempt in range(1, max_retries + 1):
                attempts = attempt
                try:
                    response = await ac.post(url, json=payload, timeout=timeout)
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "A2A delegate attempt %d/%d to '%s' failed: %s",
                        attempt,
                        max_retries,
                        peer.name,
                        last_error,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                        continue
                    break
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "A2A delegate attempt %d/%d to '%s' transport error: %s",
                        attempt,
                        max_retries,
                        peer.name,
                        last_error,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                        continue
                    break

                status = response.status_code
                if 200 <= status < 300:
                    return self._extract_remote_id(response, fallback=task.id)
                if status >= 500 or status in (408, 425, 429):
                    last_error = f"HTTP {status}: {response.text[:120]}"
                    logger.warning(
                        "A2A delegate attempt %d/%d to '%s' transient %d",
                        attempt,
                        max_retries,
                        peer.name,
                        status,
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
                        continue
                    break
                # Non-retryable client error.
                detail = response.text[:200]
                raise A2ATaskRejectedError(peer.name, status, detail)
        finally:
            if owns_client:
                await ac.aclose()

        raise A2ADelegationError(peer.name, attempts, last_error)

    @staticmethod
    def _build_payload(
        *,
        task: FederatedTask,
        sender_card: AgentCard | dict[str, Any] | None,
        local_endpoint: str,
    ) -> dict[str, Any]:
        """Compose the wire payload for ``POST /a2a/v0/tasks``."""
        if sender_card is None:
            card_dict: dict[str, Any] = {
                "name": "bernstein-orchestrator",
                "description": "Bernstein A2A federation peer",
                "endpoint": local_endpoint,
                "provider": "bernstein",
                "protocol_version": "0.1",
                "capabilities": [],
            }
        elif isinstance(sender_card, dict):
            card_dict = dict(sender_card)
        else:
            card_dict = sender_card.to_dict()
        return {
            "sender": card_dict,
            "task": {
                "id": task.id,
                "message": task.message,
                "role": task.role,
            },
        }

    @staticmethod
    def _extract_remote_id(response: httpx.Response, *, fallback: str) -> str:
        """Pull the remote task id out of a 2xx response body, with fallback."""
        try:
            data = response.json()
        except (ValueError, httpx.DecodingError):
            return fallback
        if isinstance(data, dict):
            for key in ("remote_task_id", "task_id", "id"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        return fallback

    def accept_inbound_task(
        self,
        peer_name: str,
        remote_task_id: str,
        message: str,
        role: str = "backend",
    ) -> FederatedTask:
        """Accept an inbound task from an external peer (idempotent).

        Bug fix: when the caller retries (e.g. after a transient 5xx that
        slipped through the receiver before the response was sent), the
        same ``(peer_name, remote_task_id)`` arrives twice. The previous
        implementation appended a duplicate entry to the ledger every
        time, which broke any "tasks-by-peer" or "delegated-task-count"
        invariant the orchestrator relied on. Look up the existing entry
        first and return it untouched if found.

        Args:
            peer_name: Name of the sending peer.
            remote_task_id: Task ID on the remote peer.
            message: Task description.
            role: Role hint.

        Returns:
            The accepted FederatedTask (newly created or existing one).
        """
        # Idempotent path — return the existing entry on duplicate posts.
        if remote_task_id:
            for existing_id in self._by_peer.get(peer_name, ()):
                existing = self._tasks.get(existing_id)
                if (
                    existing is not None
                    and existing.direction == "inbound"
                    and existing.remote_task_id == remote_task_id
                ):
                    # Refresh peer heartbeat even on duplicate.
                    peer = self._peers.get(peer_name)
                    if peer is not None and peer.state != PeerState.DEREGISTERED:
                        peer.last_seen = time.time()
                        peer.state = PeerState.ACTIVE
                    return existing

        task = FederatedTask(
            id=uuid.uuid4().hex[:12],
            peer_name=peer_name,
            remote_task_id=remote_task_id,
            message=message,
            role=role,
            direction="inbound",
            status=FederatedTaskStatus.ACCEPTED,
        )
        self._tasks[task.id] = task
        self._by_peer.setdefault(peer_name, []).append(task.id)
        # Refresh peer last_seen so inbound traffic counts as a heartbeat.
        peer = self._peers.get(peer_name)
        if peer is not None and peer.state != PeerState.DEREGISTERED:
            peer.last_seen = time.time()
            peer.state = PeerState.ACTIVE
        logger.info("Accepted inbound task '%s' from peer '%s'", task.id, peer_name)
        return task

    def mark_sent(self, task_id: str, remote_task_id: str) -> bool:
        """Mark an outbound task as sent, recording the remote task ID.

        Args:
            task_id: Local federation task ID.
            remote_task_id: ID assigned by the remote peer.

        Returns:
            True if the task was updated.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.remote_task_id = remote_task_id
        task.status = FederatedTaskStatus.SENT
        task.updated_at = time.time()
        return True

    def update_status(self, task_id: str, status: FederatedTaskStatus, result: str = "") -> bool:
        """Update the status of a federated task.

        Args:
            task_id: Federation task ID.
            status: New status.
            result: Optional result text.

        Returns:
            True if the task was updated.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.status = status
        task.updated_at = time.time()
        if result:
            task.result = result
        return True

    def link_local_task(self, task_id: str, local_task_id: str) -> bool:
        """Link a federated task to a local Bernstein task.

        Args:
            task_id: Federation task ID.
            local_task_id: Local Bernstein task ID.

        Returns:
            True if linked.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.local_task_id = local_task_id
        return True

    def get_task(self, task_id: str) -> FederatedTask | None:
        """Look up a federated task by ID."""
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        peer_name: str | None = None,
        direction: str | None = None,
    ) -> list[FederatedTask]:
        """List federated tasks with optional filtering.

        Args:
            peer_name: Filter by peer name.
            direction: Filter by direction ("inbound" or "outbound").

        Returns:
            List of matching federated tasks.
        """
        tasks = list(self._tasks.values())
        if peer_name is not None:
            tasks = [t for t in tasks if t.peer_name == peer_name]
        if direction is not None:
            tasks = [t for t in tasks if t.direction == direction]
        return tasks

    def to_dict(self) -> dict[str, Any]:
        """Serialize federation state to a JSON-compatible dict."""
        return {
            "local_endpoint": self._local_endpoint,
            "peers": {n: p.to_dict() for n, p in self._peers.items()},
            "task_count": len(self._tasks),
        }
