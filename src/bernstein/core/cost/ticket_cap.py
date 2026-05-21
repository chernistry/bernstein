"""Hard per-ticket cost cap with clean termination and tracker writeback.

This module extends the existing cost subsystem with a per-ticket USD
ceiling. The orchestrator dispatch loop polls
:meth:`TicketCostCapMeter.should_halt` on every tool-call boundary; once
cumulative spend on a ticket would breach the configured cap the meter
flips a soft-abort flag, persists partial-progress state under
``.sdd/runtime/halted/<ticket-id>.json``, and (best-effort) posts a
summary comment back to the originating tracker through the tracker
contract.

Default behaviour is unchanged: when ``cap_usd`` is ``None`` (or
non-positive) the meter is a no-op and existing run-level / envelope
budgets continue to apply. A cap of ``0.0`` is honoured as "halt
immediately" so dry-run / preview surfaces can short-circuit without
issuing any tool call.

The module deliberately does not depend on the orchestrator package so
it can be imported from CLI, daemon, and test contexts alike.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bernstein.core.cost.cost_tracker import CostTracker
    from bernstein.core.trackers.contract import AbstractTrackerAdapter

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HALT_REASON",
    "EXIT_CODE_TICKET_COST_CAP",
    "CostCapExceeded",
    "HaltState",
    "TicketCostCapMeter",
    "resolve_ticket_cap_usd",
    "write_halt_state",
]


# Documented exit code emitted by the dispatch loop when a per-ticket
# cost cap triggers a clean termination. Distinct from the run-wide
# budget exit code so operators can wire alerting per failure mode.
EXIT_CODE_TICKET_COST_CAP: int = 64

# Default ``reason`` string written into the halt-state file.
DEFAULT_HALT_REASON: str = "per_ticket_cost_cap_exceeded"


class CostCapExceeded(RuntimeError):
    """Raised when accumulated cost on a ticket breaches the configured cap.

    The dispatch loop catches this, persists state via
    :func:`write_halt_state`, posts a tracker comment, and exits with
    :data:`EXIT_CODE_TICKET_COST_CAP`. Callers that need the structured
    payload can read the fields directly.

    Attributes:
        ticket_id: Tracker-side identifier of the ticket that tripped.
        cost_usd: Cumulative spend on the ticket at the moment of trip.
        cap_usd: Configured per-ticket cap in USD.
        reason: Short identifier suitable for metrics labels.
    """

    def __init__(
        self,
        ticket_id: str,
        *,
        cost_usd: float,
        cap_usd: float,
        reason: str = DEFAULT_HALT_REASON,
    ) -> None:
        super().__init__(f"ticket {ticket_id!r} exceeded cost cap: spent=${cost_usd:.4f} cap=${cap_usd:.4f} ({reason})")
        self.ticket_id = ticket_id
        self.cost_usd = cost_usd
        self.cap_usd = cap_usd
        self.reason = reason


@dataclass(frozen=True)
class HaltState:
    """Structured halt-state record persisted under ``.sdd/runtime/halted/``.

    Attributes:
        ticket_id: Tracker-side identifier.
        cost_usd: Cumulative spend on the ticket at halt time.
        cap_usd: Configured per-ticket cap in USD.
        reason: Short identifier for the halt cause.
        last_tool_call_id: Identifier of the last tool call observed
            before the halt fired (``None`` when unavailable).
        partial_artefacts: Best-effort list of paths to partial work
            produced by the agent before the cap tripped.
        timestamp: Unix timestamp when the halt was recorded.
        run_id: Optional orchestrator run identifier, useful for joining
            with the run-level cost ledger.
    """

    ticket_id: str
    cost_usd: float
    cap_usd: float
    reason: str = DEFAULT_HALT_REASON
    last_tool_call_id: str | None = None
    partial_artefacts: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "ticket_id": self.ticket_id,
            "cost_usd": round(self.cost_usd, 6),
            "cap_usd": round(self.cap_usd, 6),
            "reason": self.reason,
            "last_tool_call_id": self.last_tool_call_id,
            "partial_artefacts": list(self.partial_artefacts),
            "timestamp": self.timestamp,
            "run_id": self.run_id,
        }


def resolve_ticket_cap_usd(
    *,
    ticket_cap: float | None,
    default_cap: float | None = None,
    overrides: dict[str, float] | None = None,
    override_key: str | None = None,
) -> float | None:
    """Resolve the effective per-ticket cap from layered sources.

    Precedence (highest first):
      1. ``ticket_cap`` from the ticket frontmatter (``cost_cap_usd``).
      2. A lookup in ``overrides`` keyed by ``override_key`` (typically
         ``tracker_name`` / ``role`` / ``priority``).
      3. ``default_cap`` (global default).

    Returns:
        The resolved cap, or ``None`` when no source provides one.
        ``0.0`` is preserved and means "halt immediately". Invalid or
        negative values at one layer are skipped and the resolution
        continues to the next layer.
    """
    if ticket_cap is not None:
        resolved = _normalise(ticket_cap)
        if resolved is not None:
            return resolved
    if overrides and override_key and override_key in overrides:
        resolved = _normalise(overrides[override_key])
        if resolved is not None:
            return resolved
    if default_cap is not None:
        resolved = _normalise(default_cap)
        if resolved is not None:
            return resolved
    return None


def _normalise(value: float | None) -> float | None:
    """Coerce ``value`` to ``float``; return ``None`` for unknown/negative."""
    if value is None:
        return None
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        return None
    if coerced < 0.0:
        return None
    return coerced


def write_halt_state(state: HaltState, base_dir: Path) -> Path:
    """Persist ``state`` under ``base_dir / runtime / halted / <id>.json``.

    Creates parent directories as needed and writes the file atomically
    via ``rename`` so that a partial write never leaves a malformed JSON
    blob on disk.

    Args:
        state: The halt-state record to persist.
        base_dir: The ``.sdd`` directory (or test override).

    Returns:
        Absolute path of the written file.
    """
    halted_dir = base_dir / "runtime" / "halted"
    halted_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _sanitise_id(state.ticket_id)
    final_path = halted_dir / f"{safe_id}.json"
    tmp_path = halted_dir / f"{safe_id}.json.tmp"
    tmp_path.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True))
    tmp_path.replace(final_path)
    return final_path


def _sanitise_id(ticket_id: str) -> str:
    """Return ``ticket_id`` with characters unsafe for filenames replaced."""
    cleaned = "".join(c if c.isalnum() or c in {"-", "_", "."} else "_" for c in ticket_id)
    return cleaned or "unknown"


def format_writeback_comment(state: HaltState) -> str:
    """Render the tracker comment body for a halt-state record.

    Mirrors the structured-comment schema from the acceptance criteria
    (fenced YAML with ``cost_used_usd``, ``cost_cap_usd``,
    ``stage_reached``, ``next_step_hint``) so downstream automation can
    parse the block deterministically.
    """
    stage = state.last_tool_call_id or "before_first_tool_call"
    lines = [
        "Bernstein halted this ticket: per-ticket cost cap reached.",
        "",
        "```yaml",
        f"cost_used_usd: {state.cost_usd:.4f}",
        f"cost_cap_usd: {state.cap_usd:.4f}",
        f"stage_reached: {stage}",
        f"reason: {state.reason}",
        "next_step_hint: review partial state under .sdd/runtime/halted/, "
        "raise the cap if the work item warrants more budget, then re-queue.",
        "```",
    ]
    return "\n".join(lines)


def post_writeback_comment(
    adapter: AbstractTrackerAdapter | None,
    state: HaltState,
) -> bool:
    """Best-effort post the halt summary as a tracker comment.

    Returns ``True`` when the comment was successfully posted, ``False``
    otherwise. Never raises - the dispatch loop must terminate cleanly
    regardless of tracker availability.
    """
    if adapter is None:
        logger.info(
            "ticket_cap: no tracker adapter wired; skipping writeback for %s",
            state.ticket_id,
        )
        return False
    body = format_writeback_comment(state)
    idempotency_key = f"bernstein-cost-cap-{_sanitise_id(state.ticket_id)}-{int(state.timestamp)}"
    try:
        adapter.add_comment(state.ticket_id, body, idempotency_key=idempotency_key)
    except Exception as exc:
        logger.warning(
            "ticket_cap: tracker writeback failed for %s: %s",
            state.ticket_id,
            exc,
        )
        return False
    return True


@dataclass
class TicketCostCapMeter:
    """Per-ticket cost meter that drives the soft-abort flag.

    The orchestrator dispatch loop calls :meth:`record_cost` whenever a
    new chunk of spend lands (either after a tool call or via a periodic
    pump for cheap polling) and :meth:`should_halt` before issuing the
    next tool call. When the cap is breached, the meter flips
    :attr:`halted` to ``True`` and stashes a :class:`HaltState` snapshot
    for the caller to persist + write back.

    Attributes:
        ticket_id: The ticket being metered.
        cap_usd: Configured cap in USD. ``None`` disables the meter
            (default behaviour is preserved). ``0.0`` halts immediately.
        run_id: Optional orchestrator run identifier carried into the
            halt-state payload.
        reason: Short identifier emitted in metrics and writeback.
    """

    ticket_id: str
    cap_usd: float | None
    run_id: str | None = None
    reason: str = DEFAULT_HALT_REASON

    _spent_usd: float = field(default=0.0, init=False, repr=False)
    _halted: bool = field(default=False, init=False, repr=False)
    _last_tool_call_id: str | None = field(default=None, init=False, repr=False)
    _partial_artefacts: list[str] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def spent_usd(self) -> float:
        """Cumulative spend recorded against this ticket."""
        with self._lock:
            return self._spent_usd

    @property
    def halted(self) -> bool:
        """``True`` when the cap has tripped and a halt is pending."""
        with self._lock:
            return self._halted

    @property
    def enabled(self) -> bool:
        """``True`` when the meter has a non-``None`` cap configured."""
        return self.cap_usd is not None

    def record_cost(self, cost_usd: float) -> bool:
        """Add ``cost_usd`` to the cumulative ticket total.

        Returns ``True`` when the running total now meets or exceeds the
        cap (the meter will report ``halted=True`` on the next
        :meth:`should_halt` call). When ``cost_usd <= 0`` the call is a
        no-op so callers can blindly forward token-usage records without
        gating on sign.

        Args:
            cost_usd: USD delta to add. Non-positive values are ignored.
        """
        delta = max(0.0, cost_usd)
        with self._lock:
            self._spent_usd += delta
            if self._should_halt_locked():
                self._halted = True
                return True
            return False

    def sync_from_tracker(self, tracker: CostTracker | None) -> None:
        """Reset cumulative spend from a :class:`CostTracker` ledger.

        Useful when a long-lived dispatch loop is restarted from a
        persisted ``CostTracker``: the meter rebuilds its per-ticket
        running total by summing ``TokenUsage`` rows for the metered
        ticket id. No-op when ``tracker`` is ``None``.
        """
        if tracker is None:
            return
        total = sum(u.cost_usd for u in tracker.usages if u.task_id == self.ticket_id)
        with self._lock:
            self._spent_usd = float(total)
            if self._should_halt_locked():
                self._halted = True

    def attach_partial_artefact(self, path: str | Path) -> None:
        """Register a partial-artefact path for the halt-state payload."""
        with self._lock:
            self._partial_artefacts.append(str(path))

    def note_last_tool_call(self, tool_call_id: str | None) -> None:
        """Remember the most recent tool-call id for the halt payload."""
        with self._lock:
            self._last_tool_call_id = tool_call_id

    def should_halt(self) -> bool:
        """Return the current soft-abort flag.

        Dispatch loops MUST call this before issuing the next tool call.
        Calling repeatedly is cheap (single lock acquisition + compare).
        """
        with self._lock:
            if self._halted:
                return True
            if self._should_halt_locked():
                self._halted = True
                return True
            return False

    def snapshot(self) -> HaltState:
        """Capture the current state as a :class:`HaltState` record.

        Safe to call repeatedly; the returned record is a frozen value.
        """
        with self._lock:
            cap = self.cap_usd if self.cap_usd is not None else 0.0
            return HaltState(
                ticket_id=self.ticket_id,
                cost_usd=self._spent_usd,
                cap_usd=cap,
                reason=self.reason,
                last_tool_call_id=self._last_tool_call_id,
                partial_artefacts=tuple(self._partial_artefacts),
                run_id=self.run_id,
            )

    def enforce(
        self,
        *,
        base_dir: Path,
        adapter: AbstractTrackerAdapter | None = None,
        writeback: Callable[[AbstractTrackerAdapter | None, HaltState], bool] | None = None,
    ) -> HaltState | None:
        """Drive the clean-termination sequence when the cap has tripped.

        When the meter is halted, persists the halt state under
        ``<base_dir>/runtime/halted/<ticket-id>.json`` and posts a
        writeback comment via ``adapter`` (when supplied). Returns the
        :class:`HaltState` written so the caller can raise
        :class:`CostCapExceeded` or otherwise react.

        Args:
            base_dir: The ``.sdd`` root directory.
            adapter: Optional tracker adapter for writeback.
            writeback: Override of the writeback function (used by tests
                to inject a mock). Defaults to
                :func:`post_writeback_comment`.

        Returns:
            The persisted :class:`HaltState`, or ``None`` if the meter
            has not tripped.
        """
        if not self.should_halt():
            return None
        state = self.snapshot()
        try:
            write_halt_state(state, base_dir)
        except OSError as exc:  # pragma: no cover - filesystem failures are rare
            logger.warning(
                "ticket_cap: failed to persist halt state for %s: %s",
                state.ticket_id,
                exc,
            )
        fn = writeback or post_writeback_comment
        try:
            fn(adapter, state)
        except Exception as exc:
            logger.warning(
                "ticket_cap: writeback raised for %s: %s",
                state.ticket_id,
                exc,
            )
        return state

    # ---- internal --------------------------------------------------------

    def _should_halt_locked(self) -> bool:
        cap = self.cap_usd
        if cap is None:
            return False
        # ``cap == 0.0`` halts immediately so dry-run / preview paths can
        # opt out of any spend without monkey-patching the meter.
        if cap <= 0.0:
            return True
        return self._spent_usd >= cap
