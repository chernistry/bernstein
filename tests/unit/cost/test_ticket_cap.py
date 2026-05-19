"""Tests for the hard per-ticket cost cap with clean termination.

Coverage matrix:

* No cap -> meter is a no-op even after large spends.
* Cap exceeded mid-run -> ``should_halt`` flips True and ``enforce``
  writes the halt-state file with the documented schema.
* Tracker writeback is invoked with the expected payload (mock).
* ``CostCapExceeded`` carries the ticket-id, spend, and cap.
* Edge cases: ``cap=0.0`` halts immediately, negative deltas are
  ignored, halt-state filenames are filesystem-safe.
* ``Ticket`` dataclass exposes the new ``cost_cap_usd`` field with the
  documented default (``None``).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bernstein.core.cost.ticket_cap import (
    DEFAULT_HALT_REASON,
    EXIT_CODE_TICKET_COST_CAP,
    CostCapExceeded,
    HaltState,
    TicketCostCapMeter,
    format_writeback_comment,
    post_writeback_comment,
    resolve_ticket_cap_usd,
    write_halt_state,
)
from bernstein.core.trackers.contract import Ticket

# ---------------------------------------------------------------------------
# Ticket schema
# ---------------------------------------------------------------------------


def test_ticket_cost_cap_usd_default_is_none() -> None:
    """Default behaviour is unchanged: ``cost_cap_usd`` is ``None``."""
    ticket = Ticket(
        id="t-1",
        external_url="https://example.invalid/t-1",
        title="",
        body="",
        status="open",
    )
    assert ticket.cost_cap_usd is None


def test_ticket_cost_cap_usd_round_trips_user_value() -> None:
    ticket = Ticket(
        id="t-2",
        external_url="",
        title="",
        body="",
        status="open",
        cost_cap_usd=5.0,
    )
    assert ticket.cost_cap_usd == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Meter behaviour
# ---------------------------------------------------------------------------


def test_no_cap_is_a_no_op() -> None:
    """Acceptance: no cap -> no halt."""
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=None)
    meter.record_cost(100.0)
    assert meter.enabled is False
    assert meter.should_halt() is False
    assert meter.spent_usd == pytest.approx(100.0)


def test_cap_not_hit_keeps_running() -> None:
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=2.00)
    meter.record_cost(0.25)
    meter.record_cost(0.50)
    assert meter.should_halt() is False
    assert meter.spent_usd == pytest.approx(0.75)


def test_cap_exceeded_flips_halt_flag_before_next_tool_call() -> None:
    """Acceptance: cap exceeded -> agent halts before next tool call."""
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=1.00)
    meter.record_cost(0.60)
    assert meter.should_halt() is False  # below cap
    halted = meter.record_cost(0.50)
    assert halted is True
    assert meter.halted is True
    # Subsequent should_halt() calls remain True so the dispatch loop
    # cannot accidentally clear the soft-abort flag.
    assert meter.should_halt() is True


def test_cap_zero_halts_immediately() -> None:
    """A cap of 0.0 means "no work permitted"."""
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=0.0)
    assert meter.should_halt() is True


def test_negative_delta_is_ignored() -> None:
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=1.0)
    meter.record_cost(-0.5)
    assert meter.spent_usd == pytest.approx(0.0)
    assert meter.should_halt() is False


# ---------------------------------------------------------------------------
# Halt-state persistence
# ---------------------------------------------------------------------------


def test_write_halt_state_writes_documented_schema(tmp_path: Path) -> None:
    """Acceptance: state file is written with correct schema."""
    state = HaltState(
        ticket_id="ORG-123",
        cost_usd=1.2345,
        cap_usd=1.0,
        last_tool_call_id="tool-call-7",
        partial_artefacts=("/tmp/scratch/diff.patch",),
        run_id="run-abc",
    )
    path = write_halt_state(state, tmp_path)
    assert path.exists()
    assert path.parent == tmp_path / "runtime" / "halted"
    payload = json.loads(path.read_text())
    assert payload["ticket_id"] == "ORG-123"
    assert payload["cost_usd"] == pytest.approx(1.2345)
    assert payload["cap_usd"] == pytest.approx(1.0)
    assert payload["reason"] == DEFAULT_HALT_REASON
    assert payload["last_tool_call_id"] == "tool-call-7"
    assert payload["partial_artefacts"] == ["/tmp/scratch/diff.patch"]
    assert payload["run_id"] == "run-abc"
    assert isinstance(payload["timestamp"], (int, float))


def test_write_halt_state_sanitises_unsafe_ids(tmp_path: Path) -> None:
    state = HaltState(
        ticket_id="proj/issue 42?",
        cost_usd=0.5,
        cap_usd=0.4,
    )
    path = write_halt_state(state, tmp_path)
    assert "/" not in path.name and " " not in path.name and "?" not in path.name
    assert path.exists()


def test_snapshot_carries_tool_call_and_artefacts() -> None:
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=0.10, run_id="run-1")
    meter.record_cost(0.05)
    meter.note_last_tool_call("tc-9")
    meter.attach_partial_artefact("/tmp/draft.md")
    meter.record_cost(0.10)
    assert meter.should_halt() is True
    snap = meter.snapshot()
    assert snap.ticket_id == "t-1"
    assert snap.cap_usd == pytest.approx(0.10)
    assert snap.cost_usd == pytest.approx(0.15)
    assert snap.last_tool_call_id == "tc-9"
    assert snap.partial_artefacts == ("/tmp/draft.md",)
    assert snap.run_id == "run-1"


# ---------------------------------------------------------------------------
# Tracker writeback
# ---------------------------------------------------------------------------


def test_enforce_invokes_tracker_writeback_with_expected_payload(tmp_path: Path) -> None:
    """Acceptance: tracker writeback called with expected payload (mock)."""
    meter = TicketCostCapMeter(ticket_id="ORG-7", cap_usd=0.50)
    meter.record_cost(0.30)
    meter.record_cost(0.30)
    adapter = MagicMock()
    writeback = MagicMock(return_value=True)
    state = meter.enforce(base_dir=tmp_path, adapter=adapter, writeback=writeback)
    assert state is not None
    assert state.cost_usd == pytest.approx(0.60)
    assert state.cap_usd == pytest.approx(0.50)
    writeback.assert_called_once_with(adapter, state)
    # File persisted with the correct shape.
    halt_path = tmp_path / "runtime" / "halted" / "ORG-7.json"
    assert halt_path.exists()


def test_enforce_returns_none_when_not_halted(tmp_path: Path) -> None:
    meter = TicketCostCapMeter(ticket_id="t-1", cap_usd=1.0)
    meter.record_cost(0.10)
    writeback = MagicMock()
    state = meter.enforce(base_dir=tmp_path, adapter=None, writeback=writeback)
    assert state is None
    writeback.assert_not_called()


def test_post_writeback_comment_uses_add_comment_with_idempotency_key() -> None:
    state = HaltState(ticket_id="ORG-9", cost_usd=1.0, cap_usd=0.5, run_id="run-1")
    adapter = MagicMock()
    ok = post_writeback_comment(adapter, state)
    assert ok is True
    args, kwargs = adapter.add_comment.call_args
    assert args[0] == "ORG-9"
    body = args[1]
    assert "cost_used_usd: 1.0000" in body
    assert "cost_cap_usd: 0.5000" in body
    assert "next_step_hint" in body
    assert kwargs["idempotency_key"].startswith("bernstein-cost-cap-ORG-9-")


def test_post_writeback_comment_swallows_adapter_failures() -> None:
    state = HaltState(ticket_id="ORG-9", cost_usd=1.0, cap_usd=0.5)
    adapter = MagicMock()
    adapter.add_comment.side_effect = RuntimeError("tracker offline")
    ok = post_writeback_comment(adapter, state)
    assert ok is False


def test_post_writeback_comment_handles_missing_adapter() -> None:
    state = HaltState(ticket_id="ORG-9", cost_usd=1.0, cap_usd=0.5)
    assert post_writeback_comment(None, state) is False


def test_format_writeback_comment_emits_fenced_yaml() -> None:
    state = HaltState(ticket_id="X", cost_usd=2.0, cap_usd=1.0)
    body = format_writeback_comment(state)
    assert "```yaml" in body and body.rstrip().endswith("```")
    assert "stage_reached" in body


# ---------------------------------------------------------------------------
# Resolution & CostCapExceeded
# ---------------------------------------------------------------------------


def test_cost_cap_exceeded_carries_ticket_id_and_amounts() -> None:
    exc = CostCapExceeded("t-1", cost_usd=2.0, cap_usd=1.5)
    assert exc.ticket_id == "t-1"
    assert exc.cost_usd == pytest.approx(2.0)
    assert exc.cap_usd == pytest.approx(1.5)
    assert "t-1" in str(exc)


def test_resolve_ticket_cap_usd_layered_precedence() -> None:
    assert resolve_ticket_cap_usd(ticket_cap=3.0, default_cap=1.0) == pytest.approx(3.0)
    assert resolve_ticket_cap_usd(
        ticket_cap=None,
        overrides={"qa": 2.5},
        override_key="qa",
        default_cap=1.0,
    ) == pytest.approx(2.5)
    assert resolve_ticket_cap_usd(ticket_cap=None, default_cap=1.0) == pytest.approx(1.0)
    assert resolve_ticket_cap_usd(ticket_cap=None) is None
    # Negative / invalid values fall back to the next layer.
    assert resolve_ticket_cap_usd(ticket_cap=-1.0, default_cap=0.25) == pytest.approx(0.25)


def test_exit_code_is_documented() -> None:
    assert EXIT_CODE_TICKET_COST_CAP == 64


# ---------------------------------------------------------------------------
# Cost-tracker integration
# ---------------------------------------------------------------------------


def test_sync_from_tracker_rebuilds_total_after_restart() -> None:
    from bernstein.core.cost.cost_tracker import CostTracker

    tracker = CostTracker(run_id="run-1", budget_usd=0.0)
    tracker.record(
        agent_id="a1",
        task_id="ORG-1",
        model="sonnet",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.40,
    )
    tracker.record(
        agent_id="a1",
        task_id="ORG-1",
        model="sonnet",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.40,
    )
    tracker.record(
        agent_id="a1",
        task_id="ORG-2",
        model="sonnet",
        input_tokens=10,
        output_tokens=10,
        cost_usd=0.10,
    )
    meter = TicketCostCapMeter(ticket_id="ORG-1", cap_usd=0.50)
    meter.sync_from_tracker(tracker)
    # Only the ORG-1 rows count, and the total already breaches 0.50.
    assert meter.spent_usd == pytest.approx(0.80)
    assert meter.should_halt() is True
