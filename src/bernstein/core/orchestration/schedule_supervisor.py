"""Schedule supervisor - wakes at the configured cadence and fires schedules.

The supervisor:

1. Iterates the ScheduleStore on each tick.
2. Computes the next fire instant for each schedule using the in-tree
   cron parser.
3. When ``now`` is at or past the next fire instant, it builds a
   :class:`bernstein.core.orchestration.schedule_projection.ProjectionResult`
   from ``(schedule_id, fire_time, last_state)`` and dispatches it
   through the existing trigger pipeline.
4. Records each fire in the audit chain with event_type
   ``schedule.fire`` and a payload carrying the projection_hash.
5. Honours the per-schedule misfire policy:

   - ``skip`` (default): one fire per tick; the supervisor advances
     ``last_fire_at`` to the missed instant without enqueuing a task
     for every interim window. A receipt is written so the operator
     can derive the counterfactual by replaying the lineage.
   - ``catch_up``: one fire per missed instant (bounded by a safety
     cap so a long downtime cannot blow the task queue).

Lifecycle: this class is callable from either a long-running
``bernstein schedule run`` worker OR from inside the existing
``bernstein daemon`` supervisor; both surfaces invoke ``tick``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

from bernstein.core.orchestration.schedule_projection import (
    ProjectionResult,
    project_schedule_fire,
)
from bernstein.core.planning.schedule_store import (
    ParsedCron,
    Schedule,
    ScheduleStore,
    parse_cron,
)
from bernstein.core.trigger_sources.schedule import normalize_schedule_fire

logger = logging.getLogger(__name__)

#: Hard cap on catch-up fires emitted in a single tick. Prevents a
#: long outage from blowing the task queue when the operator opted into
#: catch_up. The remaining missed windows fold into a single counterfactual
#: receipt the operator can replay.
DEFAULT_CATCH_UP_LIMIT = 16

#: Default tick cadence in seconds for the standalone worker.
DEFAULT_TICK_INTERVAL_S = 30.0

#: Event-type string written into the audit chain for each fire.
AUDIT_EVENT_TYPE = "schedule.fire"


# ---------------------------------------------------------------------------
# Cron iteration math (in-tree, deterministic)
# ---------------------------------------------------------------------------


def _next_fire_after(parsed: ParsedCron, anchor_epoch: int) -> int:
    """Return the next fire epoch strictly greater than ``anchor_epoch``.

    Iterates minute by minute starting from ``anchor_epoch + 60`` rounded
    down to the minute. Bounded scan: we never look more than 2 years
    ahead, which catches the worst case ``"0 0 29 2 *"`` (Feb 29) without
    spinning forever on an unsatisfiable expression.

    The supervisor calls this with an anchor of either ``last_fire_at``
    or ``now`` depending on whether the schedule has fired before. UTC
    only - the host timezone is not part of the deterministic contract;
    keeping cron evaluation in UTC means two operators on different
    timezones still fire on the same instant.
    """
    # Two-year scan cap (in minutes).
    max_minutes = 2 * 366 * 24 * 60

    # Round the anchor down to the minute, then step one minute forward
    # so "strictly greater than anchor" semantics hold even when the
    # anchor itself is already on a minute boundary.
    start_minute = (anchor_epoch // 60 + 1) * 60
    start_dt = datetime.fromtimestamp(start_minute, tz=UTC)

    for offset in range(max_minutes):
        candidate = start_dt + timedelta(minutes=offset)
        if (
            candidate.minute in parsed.minutes
            and candidate.hour in parsed.hours
            and candidate.month in parsed.months
            and _matches_day(parsed, candidate)
        ):
            return int(candidate.timestamp())

    raise RuntimeError(f"No fire instant found in 2 years for cron expression {parsed.raw!r}")


def _matches_day(parsed: ParsedCron, dt: datetime) -> bool:
    """POSIX cron day matching: if either ``day`` or ``weekday`` is
    restricted (not a full range), the match is the union of the two.

    Full range = the field expanded to the entire allowed set, which is
    how an operator writes ``*``. Standard 5-field cron semantics.
    """
    full_days = set(range(1, 32))
    full_weekdays = set(range(0, 7))

    days_restricted = set(parsed.days) != full_days
    weekdays_restricted = set(parsed.weekdays) != full_weekdays

    weekday_py_to_cron = (dt.weekday() + 1) % 7  # Monday=1 -> 1; Sunday=6 -> 0

    if days_restricted and weekdays_restricted:
        return dt.day in parsed.days or weekday_py_to_cron in parsed.weekdays
    if days_restricted:
        return dt.day in parsed.days
    if weekdays_restricted:
        return weekday_py_to_cron in parsed.weekdays
    return True


# ---------------------------------------------------------------------------
# Fire receipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FireReceipt:
    """Outcome of one fire attempt, persisted under runtime/schedules/receipts/.

    Receipts give the operator a replayable record of every fire and
    every skipped window (when ``misfire_policy == skip``). The
    ``schedule audit`` verb walks these alongside the HMAC chain.
    """

    schedule_id: str
    fire_time: int
    projection_hash: str
    rev: str
    prev_chain_digest: str
    chain_digest: str
    misfire_policy: str
    dispatched: bool
    skipped_windows: tuple[int, ...] = field(default_factory=tuple)
    counterfactual: bool = False


# ---------------------------------------------------------------------------
# AuditChainWriter adapter
# ---------------------------------------------------------------------------


class _AuditChainAdapter:
    """Thin adapter so the supervisor can talk to an ``AuditLog`` OR a stub.

    Tests inject an in-memory chain; production wires the existing
    ``bernstein.core.security.audit.AuditLog`` whose HMAC chain primitives
    we re-use (do NOT invent a parallel chain - see AC and ticket Notes).
    """

    def __init__(self, writer: Any) -> None:
        self._writer = writer

    @property
    def chain_tail(self) -> str:
        tail_attr = getattr(self._writer, "_prev_hmac", None)
        if isinstance(tail_attr, str):
            return tail_attr
        return ""

    def append(
        self,
        event_type: str,
        actor: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any],
    ) -> str:
        """Append a chain entry and return the new chain digest."""
        event = self._writer.log(event_type, actor, resource_type, resource_id, details)
        # ``AuditLog.log`` returns an AuditEvent with an ``hmac`` field.
        return getattr(event, "hmac", "")


# ---------------------------------------------------------------------------
# ScheduleSupervisor
# ---------------------------------------------------------------------------


@dataclass
class SupervisorStatus:
    """Snapshot of the supervisor for ``bernstein doctor``.

    Attributes:
        alive: True if the supervisor has ticked at least once in the
            doctor-defined liveness window.
        last_tick_at: Epoch of the last tick (0 if never).
        last_fire_at: Epoch of the last successful fire across all
            schedules (0 if no schedule has ever fired).
        next_fire_at: Epoch of the next due fire across all schedules
            (0 if no schedule is currently registered).
        next_fire_schedule_id: ID of the schedule the next fire belongs to.
        schedules_total: Count of registered schedules.
    """

    alive: bool
    last_tick_at: float
    last_fire_at: float
    next_fire_at: float
    next_fire_schedule_id: str
    schedules_total: int


class ScheduleSupervisor:
    """Polls the ScheduleStore and fires due schedules.

    Args:
        store: ScheduleStore instance.
        dispatch: Callable invoked with each ready TriggerEvent. The
            production wiring forwards into TriggerManager.evaluate; tests
            inject a list-append spy.
        audit_writer: Object exposing ``log(event_type, actor,
            resource_type, resource_id, details) -> AuditEvent``. The
            production wiring passes ``AuditLog``; tests pass a stub.
        catch_up_limit: Hard cap on catch-up fires per tick.
    """

    def __init__(
        self,
        store: ScheduleStore,
        dispatch: Callable[[Any], None],
        audit_writer: Any,
        *,
        catch_up_limit: int = DEFAULT_CATCH_UP_LIMIT,
    ) -> None:
        self._store = store
        self._dispatch = dispatch
        self._chain = _AuditChainAdapter(audit_writer) if audit_writer is not None else None
        self._catch_up_limit = max(1, int(catch_up_limit))
        self._receipts_dir = store.directory.parent / "schedule_receipts"
        self._receipts_dir.mkdir(parents=True, exist_ok=True)
        self._last_tick_at = 0.0
        self._last_fire_at = 0.0

    # -- Public API ---------------------------------------------------------

    def tick(self, *, now: float | None = None) -> list[FireReceipt]:
        """Run one supervisor tick.

        Returns the list of receipts emitted on this tick (mostly empty
        when no schedule is due). The list is also persisted to disk for
        ``bernstein schedule audit``.
        """
        now_epoch = int(now if now is not None else time.time())
        self._last_tick_at = float(now_epoch)

        receipts: list[FireReceipt] = []
        for schedule in self._store.list():
            try:
                receipts.extend(self._tick_one(schedule, now_epoch))
            except Exception:  # pragma: no cover - defensive
                logger.exception("Supervisor tick failed for schedule %s", schedule.id)
        return receipts

    def status(self, *, liveness_window_s: float = 120.0) -> SupervisorStatus:
        """Produce a doctor-ready snapshot.

        The ``alive`` flag is True iff the supervisor has ticked within
        ``liveness_window_s`` seconds. Operators tune the window through
        the doctor surface; we keep a generous default because a quiet
        schedule catalog still expects the supervisor to ping the store.
        """
        now = time.time()
        alive = (now - self._last_tick_at) <= liveness_window_s if self._last_tick_at else False
        schedules = self._store.list()
        last_fire = self._last_fire_at
        next_fire = 0.0
        next_id = ""
        for schedule in schedules:
            if schedule.last_fire_at > last_fire:
                last_fire = schedule.last_fire_at
            try:
                upcoming = self.next_fire_for(schedule, anchor_epoch=int(now))
            except Exception:  # pragma: no cover - defensive
                continue
            if next_fire == 0.0 or upcoming < next_fire:
                next_fire = float(upcoming)
                next_id = schedule.id
        return SupervisorStatus(
            alive=alive,
            last_tick_at=self._last_tick_at,
            last_fire_at=last_fire,
            next_fire_at=next_fire,
            next_fire_schedule_id=next_id,
            schedules_total=len(schedules),
        )

    def next_fire_for(self, schedule: Schedule, *, anchor_epoch: int) -> int:
        """Return the next fire instant for ``schedule`` after ``anchor_epoch``.

        Splits cron parsing from iteration so the supervisor can cache
        parsed cron expressions in future revs without changing the
        external API.
        """
        parsed = parse_cron(schedule.cron)
        anchor = max(anchor_epoch, int(schedule.last_fire_at))
        return _next_fire_after(parsed, anchor)

    # -- Internals ----------------------------------------------------------

    def _tick_one(self, schedule: Schedule, now_epoch: int) -> list[FireReceipt]:
        """Tick a single schedule. May emit 0..N receipts."""
        parsed = parse_cron(schedule.cron)
        anchor = int(schedule.last_fire_at) if schedule.last_fire_at else now_epoch - 60
        receipts: list[FireReceipt] = []
        skipped_windows: list[int] = []

        # Step forward through missed windows up to ``now``.
        current_anchor = anchor
        fires_dispatched = 0
        while True:
            try:
                next_fire = _next_fire_after(parsed, current_anchor)
            except RuntimeError:
                break
            if next_fire > now_epoch:
                break
            if schedule.misfire_policy == "catch_up":
                if fires_dispatched >= self._catch_up_limit:
                    # Fold remaining windows into a counterfactual receipt
                    # so the operator can replay them out-of-band.
                    skipped_windows.append(next_fire)
                    current_anchor = next_fire
                    continue
                receipts.append(self._fire(schedule, next_fire, counterfactual=False))
                fires_dispatched += 1
            else:  # skip policy
                # Only dispatch the most recent missed instant; older
                # windows are folded into the skipped_windows receipt.
                if next_fire <= now_epoch:
                    # Peek one further: if there is another miss after
                    # this one but still <= now, this current one is
                    # superseded.
                    try:
                        peek = _next_fire_after(parsed, next_fire)
                    except RuntimeError:
                        peek = None
                    if peek is not None and peek <= now_epoch:
                        skipped_windows.append(next_fire)
                        current_anchor = next_fire
                        continue
                receipts.append(self._fire(schedule, next_fire, counterfactual=False))
                fires_dispatched += 1
            current_anchor = next_fire

        if skipped_windows and schedule.misfire_policy == "skip":
            # Emit one counterfactual receipt summarising the skipped windows.
            receipts.append(
                self._record_counterfactual(schedule, skipped_windows, now_epoch),
            )
        elif skipped_windows and schedule.misfire_policy == "catch_up":
            # catch_up hit the cap; record the remainder as a counterfactual
            receipts.append(
                self._record_counterfactual(schedule, skipped_windows, now_epoch),
            )

        return receipts

    def _fire(
        self,
        schedule: Schedule,
        fire_epoch: int,
        *,
        counterfactual: bool,
    ) -> FireReceipt:
        """Build the projection, dispatch the trigger event, and chain it."""
        projection = project_schedule_fire(
            schedule_id=schedule.id,
            fire_time=fire_epoch,
            last_state=None,
            goal=schedule.goal,
            scenario_id=schedule.scenario_id,
        )

        prev_chain = self._chain.chain_tail if self._chain is not None else ""
        chain_digest = self._append_audit(schedule, projection, prev_chain, counterfactual)

        if not counterfactual:
            event = normalize_schedule_fire(
                schedule_id=schedule.id,
                fire_time=float(fire_epoch),
                goal=schedule.goal,
                scenario_id=schedule.scenario_id,
                projection_hash=projection.projection_hash,
                misfire_policy=schedule.misfire_policy,
                extra={"chain_digest": chain_digest},
            )
            try:
                self._dispatch(event)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Dispatch failed for schedule %s @ %s", schedule.id, fire_epoch)
            self._store.update_last_fire(schedule.id, float(fire_epoch))
            self._last_fire_at = max(self._last_fire_at, float(fire_epoch))

        receipt = FireReceipt(
            schedule_id=schedule.id,
            fire_time=fire_epoch,
            projection_hash=projection.projection_hash,
            rev=projection.rev,
            prev_chain_digest=prev_chain,
            chain_digest=chain_digest,
            misfire_policy=schedule.misfire_policy,
            dispatched=not counterfactual,
            skipped_windows=(),
            counterfactual=counterfactual,
        )
        self._persist_receipt(receipt)
        return receipt

    def _record_counterfactual(
        self,
        schedule: Schedule,
        skipped: list[int],
        now_epoch: int,
    ) -> FireReceipt:
        """Emit a counterfactual receipt summarising skipped windows.

        Pure record, no dispatch and no audit-chain entry. Lets the
        operator replay the counterfactual by feeding the skipped epochs
        back into the projection.
        """
        if not skipped:
            return FireReceipt(
                schedule_id=schedule.id,
                fire_time=now_epoch,
                projection_hash="",
                rev="",
                prev_chain_digest="",
                chain_digest="",
                misfire_policy=schedule.misfire_policy,
                dispatched=False,
                skipped_windows=(),
                counterfactual=True,
            )
        receipt = FireReceipt(
            schedule_id=schedule.id,
            fire_time=skipped[-1],
            projection_hash="",
            rev="",
            prev_chain_digest="",
            chain_digest="",
            misfire_policy=schedule.misfire_policy,
            dispatched=False,
            skipped_windows=tuple(skipped),
            counterfactual=True,
        )
        self._persist_receipt(receipt)
        return receipt

    def _append_audit(
        self,
        schedule: Schedule,
        projection: ProjectionResult,
        prev_chain: str,
        counterfactual: bool,
    ) -> str:
        """Append a ``schedule.fire`` entry to the audit chain.

        The payload carries ``(schedule_id, fire_time, projection_hash,
        prev_chain_digest)`` as called out in the AC. Counterfactual
        receipts skip the chain because they represent fires that did
        NOT happen; mixing them into the chain would defeat the
        byte-identical sequence guarantee.
        """
        if counterfactual or self._chain is None:
            return prev_chain
        details = {
            "schedule_id": schedule.id,
            "fire_time": projection.fire_time,
            "projection_hash": projection.projection_hash,
            "rev": projection.rev,
            "misfire_policy": schedule.misfire_policy,
            "prev_chain_digest": prev_chain,
        }
        return self._chain.append(
            event_type=AUDIT_EVENT_TYPE,
            actor="schedule_supervisor",
            resource_type="schedule",
            resource_id=schedule.id,
            details=details,
        )

    def _persist_receipt(self, receipt: FireReceipt) -> None:
        """Write a receipt to disk for ``schedule audit`` to walk later.

        Receipt filenames bake the schedule id and fire instant so two
        operators inspecting their receipts side by side can tell at a
        glance whether their byte-identical sequence held.
        """
        suffix = "counterfactual" if receipt.counterfactual else "fire"
        filename = f"{receipt.schedule_id}-{receipt.fire_time}-{suffix}.json"
        path = self._receipts_dir / filename
        payload = asdict(receipt)
        # Tuples → lists for json. asdict already does this.
        path.write_text(json.dumps(payload, sort_keys=True, indent=2))


# ---------------------------------------------------------------------------
# Receipt loader for ``schedule audit``
# ---------------------------------------------------------------------------


def load_receipts(sdd_dir: Path) -> list[FireReceipt]:
    """Load all persisted receipts in chronological order.

    Used by ``bernstein schedule audit`` to walk the recorded fire
    sequence and verify it is byte-identical to the operator
    expectation.
    """
    receipts_dir = sdd_dir / "runtime" / "schedule_receipts"
    if not receipts_dir.exists():
        return []
    out: list[FireReceipt] = []
    for path in sorted(receipts_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load receipt %s: %s", path, exc)
            continue
        try:
            out.append(
                FireReceipt(
                    schedule_id=str(data["schedule_id"]),
                    fire_time=int(data["fire_time"]),
                    projection_hash=str(data.get("projection_hash", "")),
                    rev=str(data.get("rev", "")),
                    prev_chain_digest=str(data.get("prev_chain_digest", "")),
                    chain_digest=str(data.get("chain_digest", "")),
                    misfire_policy=str(data.get("misfire_policy", "skip")),
                    dispatched=bool(data.get("dispatched", False)),
                    skipped_windows=tuple(int(x) for x in data.get("skipped_windows", [])),
                    counterfactual=bool(data.get("counterfactual", False)),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Malformed receipt %s: %s", path, exc)
    out.sort(key=lambda r: (r.fire_time, r.schedule_id))
    return out
