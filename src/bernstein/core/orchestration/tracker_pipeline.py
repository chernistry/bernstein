"""Tracker comments as a multi-agent handoff message bus.

The tracker becomes the durable, audit-trailed, human-observable message
bus for a crew of specialist agents (architect, backend, qa, security).
Each role reads and writes the same ticket; the tracker workflow itself
encodes the pipeline. There is no queue server, no DB, no service mesh:
just the tracker plus four primitives that the in-place
"filter + claim + comment + transition" pattern lacks.

Primitives shipped here
-----------------------
* :class:`PipelineStage` - typed view of a single stage in
  ``bernstein.yaml: orchestration.tracker_pipeline.pipeline_stages``.
* :class:`PipelineConfig` - typed view of the full block (stages, lock
  TTL, per-role concurrency).
* :class:`ClaimLedger` - SQLite-backed distributed claim ledger with
  ``INSERT OR FAIL`` semantics, lease TTL and ``claimer_id`` recovery.
* :func:`make_idempotency_key` - stable
  ``sha256(tracker || ticket_id || role || stage || stage_attempt)``
  key threaded through tracker writes.
* :class:`FailurePayload` /
  :func:`format_failure_comment` - structured failure taxonomy emitted
  as a fenced YAML block inside the comment body, preserving free-text
  prose around it.
* :class:`TrackerPipeline` - the stateless loop: for each tracker,
  apply per-role filters, attempt a distributed claim, dispatch to
  the role, write a structured success/failure comment, transition.
* :class:`PipelineDispatcher` protocol - the role-execution surface
  the pipeline calls. Real callers wire this to the orchestrator's
  spawn machinery; tests inject in-process fakes.

What this module deliberately omits
-----------------------------------
* The tracker adapters themselves (separate per-tracker tickets).
* Webhook ingestion (separate ticket).
* Auto-discovery of pipeline shape from a tracker's existing workflow.

Lifecycle hook
--------------
On every stage transition (success or failure) the pipeline emits the
``tracker_pipeline.handoff`` lifecycle event. Its
:class:`bernstein.core.lifecycle.hooks.LifecycleContext` carries
``tracker``, ``ticket_id``, ``role``, ``from_status``, ``to_status``,
``stage_attempt``, ``outcome`` (``"success"`` or ``"failure"``) and
``idempotency_key``. Operators wire automation (metrics dashboards,
escalation rules) without modifying the pipeline core.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from bernstein.core.lifecycle.hooks import HookRegistry
    from bernstein.core.trackers.contract import (
        AbstractTrackerAdapter,
        Ticket,
    )


log = logging.getLogger(__name__)


__all__ = [
    "ALLOWED_FAILURE_CATEGORIES",
    "ALLOWED_FAILURE_NEXT_ACTIONS",
    "DEFAULT_CLAIM_LOCK_TTL_SECONDS",
    "DEFAULT_LEDGER_RELPATH",
    "DEFAULT_PER_ROLE_MAX_IN_FLIGHT",
    "FAILURE_BLOCK_BEGIN",
    "FAILURE_BLOCK_END",
    "ClaimLedger",
    "ClaimOutcome",
    "DispatchOutcome",
    "FailurePayload",
    "PipelineConfig",
    "PipelineDispatcher",
    "PipelineStage",
    "StageHandoff",
    "TrackerPipeline",
    "TrackerPipelineError",
    "format_failure_comment",
    "format_success_comment",
    "make_idempotency_key",
    "parse_failure_block",
    "parse_success_blocks",
]


# ---------------------------------------------------------------------------
# Defaults & constants
# ---------------------------------------------------------------------------


DEFAULT_CLAIM_LOCK_TTL_SECONDS: Final[int] = 600
"""Default lease TTL for an unfinished stage claim.

A crashed worker's claim ages out after this many seconds and another
worker may pick the ticket up. Operators can shorten this for tests or
extend it for slow agents via ``claim_lock_ttl_seconds`` in YAML.
"""

DEFAULT_PER_ROLE_MAX_IN_FLIGHT: Final[int] = 1
"""Default per-role concurrency ceiling enforced by the ledger.

Tickets currently leased to one role count against this ceiling. The
loop skips dispatching new claims while the count is at or above the
ceiling for that role.
"""

DEFAULT_LEDGER_RELPATH: Final[Path] = Path("state") / "tracker_claims.db"
"""Path under ``.sdd/`` where the SQLite ledger lives by default."""

FAILURE_BLOCK_BEGIN: Final[str] = "```yaml bernstein:failure"
"""Opening fence of the structured failure block embedded in comments."""

FAILURE_BLOCK_END: Final[str] = "```"
"""Closing fence of the structured failure block embedded in comments."""

_SUCCESS_BLOCK_BEGIN: Final[str] = "```yaml bernstein:success"

ALLOWED_FAILURE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"transient", "permanent", "policy", "unknown"},
)
"""Allowed values for :class:`FailurePayload.category`.

Exposed as a module constant so downstream tools (linters, schema
generators, integration tests) can introspect the taxonomy without
reaching into the dataclass internals.
"""

ALLOWED_FAILURE_NEXT_ACTIONS: Final[frozenset[str]] = frozenset(
    {"retry", "escalate", "abandon", "manual"},
)
"""Allowed values for :class:`FailurePayload.next_action`."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TrackerPipelineError(Exception):
    """Base class for tracker-pipeline errors."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """One stage in the pipeline.

    Attributes:
        role: Bernstein role name (e.g. ``"architect"``, ``"backend"``,
            ``"qa"``, ``"security"``). Maps to the role prompts under
            ``templates/roles/``.
        claim_status: Tracker status from which this role claims a
            ticket. A ticket in any other status is invisible to this
            stage.
        success_status: Status the ticket transitions to when the role
            completes successfully.
        failure_status: Status the ticket transitions to on a failure
            that the pipeline does not retry in-stage.
        requires_prior_role: Optional role whose successful comment
            must already exist on the ticket before this stage may
            claim. Enforces the ordering of a directed pipeline.
    """

    role: str
    claim_status: str
    success_status: str
    failure_status: str
    requires_prior_role: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PipelineStage:
        """Build from a parsed YAML mapping; raise on missing required keys."""
        try:
            role = str(raw["role"])
            claim_status = str(raw["claim_status"])
            success_status = str(raw["success_status"])
            failure_status = str(raw["failure_status"])
        except KeyError as exc:
            msg = f"pipeline stage missing required key: {exc.args[0]}"
            raise TrackerPipelineError(msg) from exc
        prior_raw = raw.get("requires_prior_role")
        requires_prior = str(prior_raw) if isinstance(prior_raw, str) and prior_raw else None
        return cls(
            role=role,
            claim_status=claim_status,
            success_status=success_status,
            failure_status=failure_status,
            requires_prior_role=requires_prior,
        )


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Typed view over ``orchestration.tracker_pipeline`` in ``bernstein.yaml``.

    Attributes:
        pipeline_stages: Ordered tuple of :class:`PipelineStage` records.
        claim_lock_ttl_seconds: How long a stage claim survives without
            progress before another worker may steal it.
        per_role_max_in_flight: Maximum number of tickets a single role
            may have leased simultaneously, summed across trackers.
    """

    pipeline_stages: tuple[PipelineStage, ...] = ()
    claim_lock_ttl_seconds: int = DEFAULT_CLAIM_LOCK_TTL_SECONDS
    per_role_max_in_flight: int = DEFAULT_PER_ROLE_MAX_IN_FLIGHT

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PipelineConfig:
        """Build from a parsed YAML mapping; tolerant of missing keys."""
        stages_raw = raw.get("pipeline_stages", ())
        stages: list[PipelineStage] = []
        if isinstance(stages_raw, Iterable) and not isinstance(stages_raw, (str, bytes)):
            for item in cast(Iterable[object], stages_raw):
                if isinstance(item, Mapping):
                    stages.append(PipelineStage.from_dict(cast(Mapping[str, object], item)))
        ttl_raw = raw.get("claim_lock_ttl_seconds", DEFAULT_CLAIM_LOCK_TTL_SECONDS)
        ttl = int(ttl_raw) if isinstance(ttl_raw, (int, float)) else DEFAULT_CLAIM_LOCK_TTL_SECONDS
        max_in_flight = DEFAULT_PER_ROLE_MAX_IN_FLIGHT
        concurrency_raw = raw.get("concurrency")
        if isinstance(concurrency_raw, Mapping):
            concurrency = cast(Mapping[str, object], concurrency_raw)
            value = concurrency.get("per_role_max_in_flight", DEFAULT_PER_ROLE_MAX_IN_FLIGHT)
            if isinstance(value, (int, float)):
                max_in_flight = max(1, int(value))
        return cls(
            pipeline_stages=tuple(stages),
            claim_lock_ttl_seconds=max(1, ttl),
            per_role_max_in_flight=max_in_flight,
        )

    def stage_for_role(self, role: str) -> PipelineStage | None:
        """Return the stage owning ``role`` or ``None`` if unknown."""
        for stage in self.pipeline_stages:
            if stage.role == role:
                return stage
        return None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def make_idempotency_key(
    *,
    tracker: str,
    ticket_id: str,
    role: str,
    stage: str,
    stage_attempt: int,
) -> str:
    """Return a stable ``sha256`` idempotency key for one stage write.

    The key is the hex digest of ``tracker || ticket_id || role || stage
    || stage_attempt`` joined with ``"\\x1f"`` (ASCII unit separator).
    The separator removes ambiguity when one component contains
    characters that appear in another.

    Args:
        tracker: Tracker adapter name (e.g. ``"github_projects"``).
        ticket_id: Tracker-side ticket id.
        role: Bernstein role processing the ticket.
        stage: Stage label (typically the role name; kept separate so
            multi-stage roles remain addressable).
        stage_attempt: Zero-based attempt count for this stage.

    Returns:
        Hex digest string suitable for ``Idempotency-Key`` headers or
        in-comment fingerprints.
    """
    parts = [tracker, ticket_id, role, stage, str(stage_attempt)]
    joined = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FailurePayload:
    """Structured failure taxonomy emitted by a stage.

    Attributes:
        reason_code: Stable machine-readable code, dot-separated
            (e.g. ``"timeout"``, ``"tests.failed"``,
            ``"policy.denied"``).
        category: Coarse bucket (``"transient"``, ``"permanent"``,
            ``"policy"``, ``"unknown"``).
        transient: ``True`` when retrying the same stage is likely to
            succeed; the pipeline may flip the ticket back to the
            claim status for another attempt.
        next_action: One of ``"retry"``, ``"escalate"``,
            ``"abandon"``, ``"manual"``. Drives downstream automation.
        detail: Optional human-readable extra context. Free text but
            kept short.
    """

    reason_code: str
    category: str
    transient: bool
    next_action: str
    detail: str = ""

    def __post_init__(self) -> None:
        # Frozen dataclasses run __post_init__ for validation only.
        if not self.reason_code or not self.reason_code.strip():
            msg = "reason_code must be non-empty"
            raise TrackerPipelineError(msg)
        if self.category not in ALLOWED_FAILURE_CATEGORIES:
            msg = f"category must be one of {sorted(ALLOWED_FAILURE_CATEGORIES)}; got {self.category!r}"
            raise TrackerPipelineError(msg)
        if self.next_action not in ALLOWED_FAILURE_NEXT_ACTIONS:
            msg = f"next_action must be one of {sorted(ALLOWED_FAILURE_NEXT_ACTIONS)}; got {self.next_action!r}"
            raise TrackerPipelineError(msg)


def format_failure_comment(
    *,
    role: str,
    stage_attempt: int,
    idempotency_key: str,
    payload: FailurePayload,
    prose: str = "",
) -> str:
    """Return the comment body that wraps ``payload`` in a fenced block.

    The free-text ``prose`` (if any) renders above the fenced YAML so
    humans see the narrative first. The fence is the contract for
    downstream automation; parsers should anchor on
    :data:`FAILURE_BLOCK_BEGIN` and :data:`FAILURE_BLOCK_END`.
    """
    detail_line = ""
    if payload.detail:
        # Single-line for YAML safety. Multiline detail belongs in prose.
        safe_detail = payload.detail.replace("\n", " ").strip()
        detail_line = f"\ndetail: {_yaml_quote(safe_detail)}"
    body_lines = [
        FAILURE_BLOCK_BEGIN,
        f"role: {_yaml_quote(role)}",
        f"stage_attempt: {stage_attempt}",
        f"idempotency_key: {_yaml_quote(idempotency_key)}",
        f"reason_code: {_yaml_quote(payload.reason_code)}",
        f"category: {_yaml_quote(payload.category)}",
        f"transient: {'true' if payload.transient else 'false'}",
        f"next_action: {_yaml_quote(payload.next_action)}{detail_line}",
        FAILURE_BLOCK_END,
    ]
    block = "\n".join(body_lines)
    if prose:
        return f"{prose.strip()}\n\n{block}"
    return block


def format_success_comment(
    *,
    role: str,
    stage_attempt: int,
    idempotency_key: str,
    summary: str,
    prose: str = "",
) -> str:
    """Return the success-side counterpart of :func:`format_failure_comment`.

    Symmetric structured block lets downstream automation recognise a
    successful handoff without re-parsing free text.
    """
    safe_summary = summary.replace("\n", " ").strip()
    body_lines = [
        _SUCCESS_BLOCK_BEGIN,
        f"role: {_yaml_quote(role)}",
        f"stage_attempt: {stage_attempt}",
        f"idempotency_key: {_yaml_quote(idempotency_key)}",
        f"summary: {_yaml_quote(safe_summary)}",
        FAILURE_BLOCK_END,
    ]
    block = "\n".join(body_lines)
    if prose:
        return f"{prose.strip()}\n\n{block}"
    return block


def parse_failure_block(comment_body: str) -> dict[str, Any] | None:
    """Return the parsed failure block found in ``comment_body``, if any.

    The function is permissive: it tokenises the block as
    ``key: value`` lines without pulling in a full YAML parser. Values
    are stripped of surrounding double quotes; ``true``/``false``
    become Python booleans; integer-shaped tokens become ``int``.

    Returns:
        Parsed mapping with keys like ``reason_code``, ``category``,
        ``transient``, ``next_action``, ``detail``, plus the meta keys
        ``role``, ``stage_attempt``, ``idempotency_key``. ``None`` when
        the block is missing.
    """
    blocks = _iter_fenced_blocks(comment_body, FAILURE_BLOCK_BEGIN)
    for parsed in blocks:
        return parsed
    return None


def parse_success_blocks(comment_body: str) -> list[dict[str, Any]]:
    """Return every parsed ``bernstein:success`` block in ``comment_body``.

    Used by :class:`TrackerPipeline._stage_is_eligible` to check the
    prior-role gate via structured fields rather than raw string match,
    so cosmetic formatting changes around the fence do not silently
    break the pipeline ordering contract.
    """
    return list(_iter_fenced_blocks(comment_body, _SUCCESS_BLOCK_BEGIN))


def _iter_fenced_blocks(comment_body: str, begin_marker: str) -> Iterable[dict[str, Any]]:
    """Yield every ``begin_marker`` ... ``FAILURE_BLOCK_END`` block as a dict.

    The closing fence is the same backtick triplet used by both success
    and failure blocks. Empty blocks and blocks missing a closing fence
    are skipped.
    """
    start = 0
    while True:
        index = comment_body.find(begin_marker, start)
        if index < 0:
            return
        after_start = index + len(begin_marker)
        end = comment_body.find(FAILURE_BLOCK_END, after_start)
        if end < 0:
            return
        inner = comment_body[after_start:end].strip()
        start = end + len(FAILURE_BLOCK_END)
        if not inner:
            continue
        parsed: dict[str, Any] = {}
        for raw_line in inner.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            parsed[key.strip()] = _decode_yaml_value(value.strip())
        if parsed:
            yield parsed


def _yaml_quote(value: str) -> str:
    """Return ``value`` rendered as a double-quoted YAML scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _decode_yaml_value(token: str) -> Any:
    """Decode a single ``key: value`` right-hand side."""
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        body = token[1:-1]
        return body.replace('\\"', '"').replace("\\\\", "\\")
    if token == "true":
        return True
    if token == "false":
        return False
    if token.lstrip("-").isdigit():
        try:
            return int(token)
        except ValueError:
            return token
    return token


# ---------------------------------------------------------------------------
# Claim ledger (SQLite-backed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """Result of a single claim attempt.

    Attributes:
        granted: ``True`` when this caller now owns the claim.
        reason: Short reason code when ``granted`` is ``False``. One of
            ``"held"``, ``"prior_role_missing"``,
            ``"concurrency_ceiling"``, ``"ledger_error"``.
        claimer_id: ``claimer_id`` of the winning caller. When the
            current call won, equals the caller's id; otherwise the id
            that already holds the lease.
        lease_expires_at: Unix timestamp the claim expires. Zero when
            no claim is held.
    """

    granted: bool
    reason: str
    claimer_id: str
    lease_expires_at: float


class ClaimLedger:
    """SQLite-backed distributed claim ledger.

    The ledger keys claims by ``(tracker, ticket_id, role)`` and uses
    ``INSERT OR FAIL`` semantics so two agents racing for the same
    ticket+role on the same tick produce exactly one INSERT success and
    one INSERT failure. The losing caller is told the holder's
    ``claimer_id`` so retries can short-circuit cleanly.

    Lease TTL handles the crashed-worker case: when a claim's
    ``lease_expires_at`` is in the past, the next caller's
    :meth:`try_claim` re-acquires it.

    The implementation pins ``check_same_thread=False`` and serialises
    writes via a per-database process-local lock; the underlying
    SQLite connection is opened lazily so test code may instantiate
    many ledgers without paying file-system cost up front.
    """

    _SCHEMA: Final[str] = """
        CREATE TABLE IF NOT EXISTS claims (
            tracker TEXT NOT NULL,
            ticket_id TEXT NOT NULL,
            role TEXT NOT NULL,
            claimer_id TEXT NOT NULL,
            lease_expires_at REAL NOT NULL,
            stage_attempt INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            PRIMARY KEY (tracker, ticket_id, role)
        )
    """
    _locks: ClassVar[dict[str, threading.RLock]] = {}
    _locks_guard: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = self._lock_for_path(db_path)

    @property
    def db_path(self) -> Path:
        """Filesystem path the ledger persists at."""
        return self._db_path

    @classmethod
    def _lock_for_path(cls, db_path: Path) -> threading.RLock:
        key = str(db_path.expanduser().resolve(strict=False))
        with cls._locks_guard:
            lock = cls._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._locks[key] = lock
            return lock

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is not None:
                return self._conn
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                isolation_level=None,  # autocommit; we use explicit BEGIN IMMEDIATE
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(self._SCHEMA)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_claims_role ON claims(role)",
            )
            self._conn = conn
            return conn

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ------------------------------------------------------------------
    # Claim lifecycle
    # ------------------------------------------------------------------

    def try_claim(
        self,
        *,
        tracker: str,
        ticket_id: str,
        role: str,
        claimer_id: str,
        ttl_seconds: int,
        per_role_max_in_flight: int,
        now: float | None = None,
    ) -> ClaimOutcome:
        """Attempt to claim ``(tracker, ticket_id, role)`` for ``claimer_id``.

        The method is atomic relative to other callers using the same
        ledger file. Concurrency-ceiling enforcement happens inside the
        same transaction so two callers cannot simultaneously push a
        role over its ceiling.

        Args:
            tracker: Tracker adapter name.
            ticket_id: Tracker-side ticket id.
            role: Bernstein role name.
            claimer_id: Unique caller identifier (typically a worker
                process id + UUID). Used for ownership and recovery.
            ttl_seconds: Lease duration; the claim expires at
                ``now + ttl_seconds``.
            per_role_max_in_flight: Maximum simultaneous live claims
                this role may hold. Pass an integer >= 1.
            now: Optional clock override; defaults to ``time.time()``.

        Returns:
            :class:`ClaimOutcome` describing whether the claim was
            granted and, on failure, why.
        """
        current = float(time.time() if now is None else now)
        expires_at = current + max(1, ttl_seconds)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Concurrency-ceiling check: count non-expired claims for the role.
                row = conn.execute(
                    "SELECT COUNT(*) FROM claims WHERE role = ? AND lease_expires_at > ?",
                    (role, current),
                ).fetchone()
                in_flight = int(row[0]) if row else 0
                # Drop expired rows so the next INSERT can succeed.
                conn.execute(
                    "DELETE FROM claims WHERE tracker = ? AND ticket_id = ? AND role = ? AND lease_expires_at <= ?",
                    (tracker, ticket_id, role, current),
                )
                existing = conn.execute(
                    "SELECT claimer_id, lease_expires_at FROM claims WHERE tracker = ? AND ticket_id = ? AND role = ?",
                    (tracker, ticket_id, role),
                ).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK")
                    return ClaimOutcome(
                        granted=False,
                        reason="held",
                        claimer_id=str(existing[0]),
                        lease_expires_at=float(existing[1]),
                    )
                if per_role_max_in_flight >= 1 and in_flight >= per_role_max_in_flight:
                    conn.execute("ROLLBACK")
                    return ClaimOutcome(
                        granted=False,
                        reason="concurrency_ceiling",
                        claimer_id="",
                        lease_expires_at=0.0,
                    )
                try:
                    conn.execute(
                        "INSERT OR FAIL INTO claims "
                        "(tracker, ticket_id, role, claimer_id, lease_expires_at, stage_attempt, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (tracker, ticket_id, role, claimer_id, expires_at, current),
                    )
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK")
                    row2 = conn.execute(
                        "SELECT claimer_id, lease_expires_at FROM claims "
                        "WHERE tracker = ? AND ticket_id = ? AND role = ?",
                        (tracker, ticket_id, role),
                    ).fetchone()
                    if row2 is None:
                        return ClaimOutcome(
                            granted=False,
                            reason="ledger_error",
                            claimer_id="",
                            lease_expires_at=0.0,
                        )
                    return ClaimOutcome(
                        granted=False,
                        reason="held",
                        claimer_id=str(row2[0]),
                        lease_expires_at=float(row2[1]),
                    )
                conn.execute("COMMIT")
            except sqlite3.OperationalError:
                log.exception("tracker_pipeline: ledger transaction failed")
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                return ClaimOutcome(
                    granted=False,
                    reason="ledger_error",
                    claimer_id="",
                    lease_expires_at=0.0,
                )
        return ClaimOutcome(
            granted=True,
            reason="granted",
            claimer_id=claimer_id,
            lease_expires_at=expires_at,
        )

    def live_claims(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Return live (non-expired) claims as ordered dicts.

        Used by ``bernstein pipeline status`` and tests to render the
        live in-flight view without re-implementing the schema or
        opening a separate sqlite connection. ``now`` is overridable so
        callers can render a deterministic snapshot.
        """
        current = float(time.time() if now is None else now)
        with self._lock:
            cursor = self._connect().execute(
                "SELECT tracker, ticket_id, role, claimer_id, lease_expires_at, "
                "stage_attempt FROM claims WHERE lease_expires_at > ? "
                "ORDER BY tracker, role, ticket_id",
                (current,),
            )
            rows: list[dict[str, Any]] = [
                {
                    "tracker": tracker,
                    "ticket_id": ticket_id,
                    "role": role,
                    "claimer_id": claimer_id,
                    "stage_attempt": int(attempt),
                    "lease_seconds_remaining": float(expires) - current,
                }
                for tracker, ticket_id, role, claimer_id, expires, attempt in cursor.fetchall()
            ]
            return rows

    def release(self, *, tracker: str, ticket_id: str, role: str, claimer_id: str) -> bool:
        """Drop the claim if ``claimer_id`` still owns it.

        Returns ``True`` when a row was removed.
        """
        with self._lock:
            cursor = self._connect().execute(
                "DELETE FROM claims WHERE tracker = ? AND ticket_id = ? AND role = ? AND claimer_id = ?",
                (tracker, ticket_id, role, claimer_id),
            )
            return bool(cursor.rowcount)

    def attempt_count(self, *, tracker: str, ticket_id: str, role: str) -> int:
        """Return ``stage_attempt`` for the live or last claim row, or ``0``."""
        with self._lock:
            row = (
                self._connect()
                .execute(
                    "SELECT stage_attempt FROM claims WHERE tracker = ? AND ticket_id = ? AND role = ?",
                    (tracker, ticket_id, role),
                )
                .fetchone()
            )
            if row is None:
                return 0
            return int(row[0])

    def bump_attempt(self, *, tracker: str, ticket_id: str, role: str, claimer_id: str) -> int:
        """Increment and return ``stage_attempt`` for the held claim.

        Returns ``-1`` when no live claim exists for ``claimer_id``.
        """
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT stage_attempt FROM claims "
                    "WHERE tracker = ? AND ticket_id = ? AND role = ? AND claimer_id = ?",
                    (tracker, ticket_id, role, claimer_id),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return -1
                attempt = int(row[0]) + 1
                conn.execute(
                    "UPDATE claims SET stage_attempt = ? "
                    "WHERE tracker = ? AND ticket_id = ? AND role = ? AND claimer_id = ?",
                    (attempt, tracker, ticket_id, role, claimer_id),
                )
                conn.execute("COMMIT")
            except sqlite3.Error:
                conn.execute("ROLLBACK")
                raise
            return attempt


# ---------------------------------------------------------------------------
# Dispatcher protocol & outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """Result of role-execution for one ticket.

    Attributes:
        success: ``True`` when the role completed cleanly.
        summary: Free-text summary for the success comment body.
        failure: Structured failure payload; required when
            ``success`` is ``False``.
        prose: Optional human-readable prose to render above the
            structured block.
    """

    success: bool
    summary: str = ""
    failure: FailurePayload | None = None
    prose: str = ""


@runtime_checkable
class PipelineDispatcher(Protocol):
    """Role-execution surface the pipeline calls per ticket.

    Real callers wire this to the orchestrator's spawn machinery.
    Tests inject in-process fakes. The dispatcher MUST be deterministic
    enough that ``DispatchOutcome.failure`` carries an actionable
    ``reason_code``; the pipeline does not re-classify failures.
    """

    def dispatch(
        self,
        *,
        tracker: str,
        ticket: Ticket,
        role: str,
        stage_attempt: int,
        idempotency_key: str,
    ) -> DispatchOutcome:
        """Run ``role`` against ``ticket`` and return an outcome."""
        ...


# ---------------------------------------------------------------------------
# Handoff record (emitted via lifecycle hook)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageHandoff:
    """One stage transition emitted to the ``tracker_pipeline.handoff`` hook."""

    tracker: str
    ticket_id: str
    role: str
    from_status: str
    to_status: str
    stage_attempt: int
    outcome: str  # "success" | "failure"
    idempotency_key: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "tracker": self.tracker,
            "ticket_id": self.ticket_id,
            "role": self.role,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "stage_attempt": self.stage_attempt,
            "outcome": self.outcome,
            "idempotency_key": self.idempotency_key,
        }


def _new_handoff_log() -> list[StageHandoff]:
    return []


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


HANDOFF_EVENT_NAME: Final[str] = "tracker_pipeline.handoff"
"""String key the pipeline emits to ``HookRegistry`` for handoff events.

We deliberately use the string form rather than declaring a new
:class:`bernstein.core.lifecycle.hooks.LifecycleEvent` member: callers
that register a callable hook accept the string event, and we avoid a
core enum churn from this leaf module. Operators who prefer a typed
event can subscribe via the script-hook layer.
"""


@dataclass
class TrackerPipeline:
    """Stateless loop turning tracker comments into a handoff bus.

    The loop is deliberately stateless: each :meth:`tick` walks the
    configured trackers in declared order, applies role-specific
    filters, claims via the ledger, dispatches, and transitions. State
    that must survive a crash lives in the SQLite ledger and in the
    tracker itself.

    Args:
        config: Typed config view.
        trackers: Mapping of adapter name -> adapter instance. The
            pipeline pulls open tickets from each adapter in turn.
        ledger: Shared :class:`ClaimLedger`.
        dispatcher: Role-execution surface.
        claimer_id: Unique identifier for this worker process. When
            ``None`` the pipeline generates one from PID + UUID.
        hook_registry: Optional :class:`HookRegistry` to receive
            ``tracker_pipeline.handoff`` callbacks. The pipeline keeps
            a single :class:`StageHandoff` payload per emitted event.

    The pipeline never raises out of :meth:`tick`. Per-ticket errors
    are logged and recorded as failure transitions where possible so
    one broken ticket cannot wedge the loop for a healthy tenant.
    """

    config: PipelineConfig
    trackers: Mapping[str, AbstractTrackerAdapter]
    ledger: ClaimLedger
    dispatcher: PipelineDispatcher
    claimer_id: str = field(default_factory=lambda: f"worker-{uuid.uuid4().hex[:12]}")
    hook_registry: HookRegistry | None = None
    handoffs: list[StageHandoff] = field(default_factory=_new_handoff_log)
    """In-process log of handoffs emitted by the most recent ticks.

    Operators who do not wire a :class:`HookRegistry` can still inspect
    ``handoffs`` to drive dashboards or tests. The list is cumulative;
    callers may clear it between sweeps.
    """

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def tick(self) -> int:
        """Run one sweep across configured trackers, returning handoff count.

        Returns:
            Number of stage transitions emitted by this sweep
            (successes + failures). Useful for adaptive polling loops.
        """
        emitted = 0
        for tracker_name, adapter in self.trackers.items():
            for stage in self.config.pipeline_stages:
                emitted += self._sweep_stage(tracker_name, adapter, stage)
        return emitted

    def open_handoffs(self) -> list[dict[str, Any]]:
        """Return the in-process handoff log as serialisable dicts.

        Used by ``bernstein pipeline status`` to render open handoffs
        across configured trackers without re-pulling tickets.
        """
        return [h.to_payload() for h in self.handoffs]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sweep_stage(
        self,
        tracker_name: str,
        adapter: AbstractTrackerAdapter,
        stage: PipelineStage,
    ) -> int:
        """Process every ticket eligible for ``stage`` on ``adapter``."""
        emitted = 0
        try:
            iterator = adapter.pull_open_tickets({"status": stage.claim_status})
        except Exception:
            log.exception(
                "tracker_pipeline: pull failed tracker=%s role=%s",
                tracker_name,
                stage.role,
            )
            return 0
        for ticket in iterator:
            try:
                if not self._stage_is_eligible(adapter, ticket, stage):
                    continue
                outcome = self.ledger.try_claim(
                    tracker=tracker_name,
                    ticket_id=ticket.id,
                    role=stage.role,
                    claimer_id=self.claimer_id,
                    ttl_seconds=self.config.claim_lock_ttl_seconds,
                    per_role_max_in_flight=self.config.per_role_max_in_flight,
                )
                if not outcome.granted:
                    if outcome.reason == "concurrency_ceiling":
                        # No point looking at remaining tickets for this
                        # role until somebody releases.
                        break
                    continue
                attempt = self.ledger.bump_attempt(
                    tracker=tracker_name,
                    ticket_id=ticket.id,
                    role=stage.role,
                    claimer_id=self.claimer_id,
                )
                if attempt < 0:
                    # Race: someone released between try_claim and bump.
                    continue
                self._process_ticket(tracker_name, adapter, ticket, stage, attempt)
                emitted += 1
            except Exception:
                log.exception(
                    "tracker_pipeline: ticket %s/%s failed",
                    tracker_name,
                    ticket.id,
                )
                self.ledger.release(
                    tracker=tracker_name,
                    ticket_id=ticket.id,
                    role=stage.role,
                    claimer_id=self.claimer_id,
                )
        return emitted

    def _stage_is_eligible(
        self,
        adapter: AbstractTrackerAdapter,
        ticket: Ticket,
        stage: PipelineStage,
    ) -> bool:
        """Check the optional prior-role gate via structured parsing.

        Earlier revisions did a raw substring match on the rendered
        ``role: "<name>"`` line; small formatting changes (quoting
        style, extra fields, fence spacing) would silently break the
        gate. We now lift every ``bernstein:success`` block and look up
        its ``role`` key, so the gate remains stable across cosmetic
        changes in the comment renderer.
        """
        required_role = stage.requires_prior_role
        if not required_role:
            return True
        # Inspect ticket body plus recent free-text comments when the
        # adapter exposes a ``list_comments`` hook. The adapter contract
        # does not yet mandate one; we degrade to body-only matching
        # when the adapter does not provide it.
        haystacks: list[str] = [ticket.body or ""]
        list_comments_raw = getattr(adapter, "list_comments", None)
        if callable(list_comments_raw):
            list_comments = cast(Callable[[str], Iterable[object]], list_comments_raw)
            try:
                for comment in list_comments(ticket.id):
                    body = getattr(comment, "body", "")
                    if body:
                        haystacks.append(body)
            except Exception:
                log.debug(
                    "tracker_pipeline: list_comments failed for %s; using body-only",
                    ticket.id,
                    exc_info=True,
                )
        for text in haystacks:
            for block in parse_success_blocks(text):
                if block.get("role") == required_role:
                    return True
        return False

    def _process_ticket(
        self,
        tracker_name: str,
        adapter: AbstractTrackerAdapter,
        ticket: Ticket,
        stage: PipelineStage,
        attempt: int,
    ) -> None:
        idempotency_key = make_idempotency_key(
            tracker=tracker_name,
            ticket_id=ticket.id,
            role=stage.role,
            stage=stage.role,
            stage_attempt=attempt,
        )
        try:
            outcome = self.dispatcher.dispatch(
                tracker=tracker_name,
                ticket=ticket,
                role=stage.role,
                stage_attempt=attempt,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            log.exception(
                "tracker_pipeline: dispatcher raised tracker=%s role=%s",
                tracker_name,
                stage.role,
            )
            outcome = DispatchOutcome(
                success=False,
                failure=FailurePayload(
                    reason_code="dispatch.exception",
                    category="unknown",
                    transient=False,
                    next_action="manual",
                    detail=str(exc)[:200],
                ),
            )
        try:
            self._write_outcome_to_tracker(
                tracker_name=tracker_name,
                adapter=adapter,
                ticket=ticket,
                stage=stage,
                attempt=attempt,
                idempotency_key=idempotency_key,
                outcome=outcome,
            )
        finally:
            if outcome.success or (outcome.failure and not outcome.failure.transient):
                self.ledger.release(
                    tracker=tracker_name,
                    ticket_id=ticket.id,
                    role=stage.role,
                    claimer_id=self.claimer_id,
                )

    def _write_outcome_to_tracker(
        self,
        *,
        tracker_name: str,
        adapter: AbstractTrackerAdapter,
        ticket: Ticket,
        stage: PipelineStage,
        attempt: int,
        idempotency_key: str,
        outcome: DispatchOutcome,
    ) -> None:
        target_status: str
        if outcome.success:
            comment_body = format_success_comment(
                role=stage.role,
                stage_attempt=attempt,
                idempotency_key=idempotency_key,
                summary=outcome.summary or "ok",
                prose=outcome.prose,
            )
            target_status = stage.success_status
        else:
            payload = outcome.failure or FailurePayload(
                reason_code="unknown.failure",
                category="unknown",
                transient=False,
                next_action="manual",
            )
            comment_body = format_failure_comment(
                role=stage.role,
                stage_attempt=attempt,
                idempotency_key=idempotency_key,
                payload=payload,
                prose=outcome.prose,
            )
            target_status = stage.failure_status if not payload.transient else stage.claim_status
        comment_key = f"{idempotency_key}:comment"
        transition_key = f"{idempotency_key}:transition"
        try:
            adapter.add_comment(
                ticket.id,
                comment_body,
                idempotency_key=comment_key,
            )
        except Exception:
            log.exception(
                "tracker_pipeline: add_comment failed tracker=%s ticket=%s",
                tracker_name,
                ticket.id,
            )
            return
        try:
            adapter.transition(
                ticket.id,
                target_status,
                idempotency_key=transition_key,
                etag=ticket.etag,
            )
        except Exception:
            log.exception(
                "tracker_pipeline: transition failed tracker=%s ticket=%s -> %s",
                tracker_name,
                ticket.id,
                target_status,
            )
            return
        handoff = StageHandoff(
            tracker=tracker_name,
            ticket_id=ticket.id,
            role=stage.role,
            from_status=stage.claim_status,
            to_status=target_status,
            stage_attempt=attempt,
            outcome="success" if outcome.success else "failure",
            idempotency_key=idempotency_key,
        )
        self.handoffs.append(handoff)
        self._emit_handoff(handoff)

    def _emit_handoff(self, handoff: StageHandoff) -> None:
        if self.hook_registry is None:
            return
        # Imported lazily so a missing ``bernstein.core.lifecycle`` does
        # not break tests that exercise the loop in isolation.
        try:
            from bernstein.core.lifecycle.hooks import (
                LifecycleContext,
                LifecycleEvent,
            )
        except Exception:
            log.debug("tracker_pipeline: lifecycle module unavailable", exc_info=True)
            return
        # Use the closest cross-CLI event - the registry tolerates
        # callables registered against any event we ask it about. We
        # add a ``handoff_event_name`` key so subscribers can filter.
        ctx = LifecycleContext(
            event=LifecycleEvent.POST_TASK,
            task=handoff.ticket_id,
            data={"handoff_event_name": HANDOFF_EVENT_NAME} | handoff.to_payload(),
        )
        try:
            self.hook_registry.run(LifecycleEvent.POST_TASK, ctx)
        except Exception:
            log.exception("tracker_pipeline: handoff hook raised")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def default_ledger_path(state_root: Path) -> Path:
    """Return the conventional ledger path under ``state_root``.

    Typically ``state_root`` is the project's ``.sdd/`` directory.
    """
    return state_root / DEFAULT_LEDGER_RELPATH


def build_pipeline_from_yaml(
    raw: Mapping[str, object],
    *,
    trackers: Mapping[str, AbstractTrackerAdapter],
    dispatcher: PipelineDispatcher,
    state_root: Path,
    hook_registry: HookRegistry | None = None,
) -> TrackerPipeline:
    """Assemble a :class:`TrackerPipeline` from the YAML ``raw`` view.

    ``raw`` is the contents of ``orchestration.tracker_pipeline`` from
    ``bernstein.yaml``. The ledger lives under
    ``state_root / DEFAULT_LEDGER_RELPATH``.
    """
    config = PipelineConfig.from_dict(raw)
    ledger = ClaimLedger(default_ledger_path(state_root))
    return TrackerPipeline(
        config=config,
        trackers=trackers,
        ledger=ledger,
        dispatcher=dispatcher,
        hook_registry=hook_registry,
    )


# Re-exported helpers used by callers wiring the pipeline up.
def stage_attempt_for(
    ledger: ClaimLedger,
    *,
    tracker: str,
    ticket_id: str,
    role: str,
) -> int:
    """Convenience: return the current stage_attempt or ``0`` if absent."""
    return ledger.attempt_count(tracker=tracker, ticket_id=ticket_id, role=role)


def role_names_in_flight(handoffs: Sequence[StageHandoff]) -> dict[str, int]:
    """Return per-role counts from a sequence of :class:`StageHandoff`.

    Used by ``bernstein pipeline status`` and tests to confirm the
    concurrency ceiling was respected over a window.
    """
    counts: dict[str, int] = {}
    for handoff in handoffs:
        if handoff.outcome == "failure":
            continue
        counts[handoff.role] = counts.get(handoff.role, 0) + 1
    return counts
