"""Unit tests for :mod:`bernstein.core.orchestration.tracker_pipeline`.

Coverage focuses on the behaviours operators rely on in production:

* Two-agent race against the same ticket+role: exactly one wins.
* Idempotency keys are stable across retries of the same stage attempt
  and differ across attempts.
* Concurrency ceiling for a role is honoured even when many tickets
  match the claim filter.
* Failure-comment block validates against the structured taxonomy
  (round-tripping through :func:`parse_failure_block`).
* End-to-end happy path: pipeline tick claims, dispatches, comments,
  transitions, and emits a handoff payload.
* Lifecycle hook payload carries the documented keys.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier, Thread
from typing import Any

import pytest

from bernstein.core.orchestration.tracker_pipeline import (
    DEFAULT_LEDGER_RELPATH,
    FAILURE_BLOCK_BEGIN,
    FAILURE_BLOCK_END,
    ClaimLedger,
    ClaimOutcome,
    DispatchOutcome,
    FailurePayload,
    PipelineConfig,
    PipelineStage,
    StageHandoff,
    TrackerPipeline,
    TrackerPipelineError,
    format_failure_comment,
    format_success_comment,
    make_idempotency_key,
    parse_failure_block,
    parse_success_blocks,
    role_names_in_flight,
)
from bernstein.core.trackers.contract import (
    AbstractTrackerAdapter,
    CommentResult,
    Ticket,
    TransitionResult,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeTrackerAdapter(AbstractTrackerAdapter):
    """In-memory adapter that satisfies :class:`AbstractTrackerAdapter`."""

    name: str = "fake"
    tickets: list[Ticket] = field(default_factory=list)
    comments: list[tuple[str, str, str | None]] = field(default_factory=list)
    transitions: list[tuple[str, str, str | None]] = field(default_factory=list)
    comments_per_ticket: dict[str, list[str]] = field(default_factory=dict)

    def pull_open_tickets(self, filter: dict[str, Any] | None = None) -> Iterator[Ticket]:
        status = (filter or {}).get("status") if filter else None
        for ticket in self.tickets:
            if status is None or ticket.status == status:
                yield ticket

    def add_comment(
        self,
        ticket_id: str,
        body: str,
        *,
        idempotency_key: str | None = None,
    ) -> CommentResult:
        comment_id = f"c{len(self.comments) + 1}"
        self.comments.append((ticket_id, body, idempotency_key))
        self.comments_per_ticket.setdefault(ticket_id, []).append(body)
        return CommentResult(comment_id=comment_id, ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> TransitionResult:
        self.transitions.append((ticket_id, status_id, idempotency_key))
        return TransitionResult(ticket_id=ticket_id, new_status=status_id, etag=etag)


@dataclass
class RecordingDispatcher:
    """Captures dispatch calls; configurable outcome per (ticket, role)."""

    plan: dict[tuple[str, str], DispatchOutcome] = field(default_factory=dict)
    calls: list[tuple[str, str, str, int, str]] = field(default_factory=list)

    def dispatch(
        self,
        *,
        tracker: str,
        ticket: Ticket,
        role: str,
        stage_attempt: int,
        idempotency_key: str,
    ) -> DispatchOutcome:
        self.calls.append((tracker, ticket.id, role, stage_attempt, idempotency_key))
        return self.plan.get(
            (ticket.id, role),
            DispatchOutcome(success=True, summary="ok"),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ticket(ticket_id: str, *, status: str = "todo", body: str = "") -> Ticket:
    return Ticket(
        id=ticket_id,
        external_url=f"https://example.test/{ticket_id}",
        title=f"ticket {ticket_id}",
        body=body,
        status=status,
    )


def _config(stages: list[PipelineStage], *, max_in_flight: int = 1) -> PipelineConfig:
    return PipelineConfig(
        pipeline_stages=tuple(stages),
        claim_lock_ttl_seconds=300,
        per_role_max_in_flight=max_in_flight,
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotencyKey:
    def test_stable_across_calls(self) -> None:
        first = make_idempotency_key(
            tracker="github_projects",
            ticket_id="T-1",
            role="backend",
            stage="backend",
            stage_attempt=0,
        )
        second = make_idempotency_key(
            tracker="github_projects",
            ticket_id="T-1",
            role="backend",
            stage="backend",
            stage_attempt=0,
        )
        assert first == second
        assert len(first) == 64  # sha256 hex digest length

    def test_changes_when_attempt_changes(self) -> None:
        a = make_idempotency_key(
            tracker="x",
            ticket_id="T-1",
            role="qa",
            stage="qa",
            stage_attempt=0,
        )
        b = make_idempotency_key(
            tracker="x",
            ticket_id="T-1",
            role="qa",
            stage="qa",
            stage_attempt=1,
        )
        assert a != b

    def test_changes_with_each_input(self) -> None:
        base = dict(tracker="x", ticket_id="T-1", role="qa", stage="qa", stage_attempt=0)
        keys = {make_idempotency_key(**base)}
        for field_name in ("tracker", "ticket_id", "role", "stage"):
            mutated = base.copy()
            mutated[field_name] = "OTHER"
            keys.add(make_idempotency_key(**mutated))
        assert len(keys) == 5

    def test_separator_isolation(self) -> None:
        """Inputs containing other inputs do not collide due to the unit separator."""
        a = make_idempotency_key(
            tracker="a",
            ticket_id="bcdefg",
            role="qa",
            stage="qa",
            stage_attempt=0,
        )
        b = make_idempotency_key(
            tracker="abcdef",
            ticket_id="g",
            role="qa",
            stage="qa",
            stage_attempt=0,
        )
        assert a != b


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------


class TestFailurePayload:
    def test_round_trip_through_block(self) -> None:
        payload = FailurePayload(
            reason_code="tests.failed",
            category="transient",
            transient=True,
            next_action="retry",
            detail="three pytest cases red",
        )
        comment = format_failure_comment(
            role="qa",
            stage_attempt=1,
            idempotency_key="abc123",
            payload=payload,
            prose="qa run summary",
        )
        assert "qa run summary" in comment
        assert FAILURE_BLOCK_BEGIN in comment
        assert comment.rstrip().endswith(FAILURE_BLOCK_END)
        parsed = parse_failure_block(comment)
        assert parsed is not None
        assert parsed["reason_code"] == "tests.failed"
        assert parsed["category"] == "transient"
        assert parsed["transient"] is True
        assert parsed["next_action"] == "retry"
        assert parsed["detail"] == "three pytest cases red"
        assert parsed["role"] == "qa"
        assert parsed["stage_attempt"] == 1
        assert parsed["idempotency_key"] == "abc123"

    def test_missing_block_returns_none(self) -> None:
        assert parse_failure_block("plain prose without a block") is None

    @pytest.mark.parametrize(
        ("category", "next_action"),
        [
            ("invalid", "retry"),
            ("transient", "nope"),
        ],
    )
    def test_rejects_invalid_taxonomy(self, category: str, next_action: str) -> None:
        with pytest.raises(TrackerPipelineError):
            FailurePayload(
                reason_code="boom",
                category=category,
                transient=True,
                next_action=next_action,
            )

    def test_rejects_empty_reason_code(self) -> None:
        with pytest.raises(TrackerPipelineError):
            FailurePayload(
                reason_code="   ",
                category="permanent",
                transient=False,
                next_action="manual",
            )

    def test_success_block_shape(self) -> None:
        block = format_success_comment(
            role="backend",
            stage_attempt=0,
            idempotency_key="key",
            summary="patch landed",
        )
        assert "```yaml bernstein:success" in block
        assert 'role: "backend"' in block
        assert 'summary: "patch landed"' in block

    def test_parse_success_blocks_returns_each_block(self) -> None:
        body = (
            "first handoff\n\n"
            + format_success_comment(role="architect", stage_attempt=0, idempotency_key="k1", summary="design ready")
            + "\n\nsecond handoff\n\n"
            + format_success_comment(role="backend", stage_attempt=0, idempotency_key="k2", summary="patch landed")
        )
        blocks = parse_success_blocks(body)
        assert [b["role"] for b in blocks] == ["architect", "backend"]
        assert blocks[0]["summary"] == "design ready"
        assert blocks[0]["idempotency_key"] == "k1"

    def test_parse_success_blocks_handles_no_match(self) -> None:
        assert parse_success_blocks("only prose, no fenced block") == []


# ---------------------------------------------------------------------------
# Claim ledger
# ---------------------------------------------------------------------------


class TestClaimLedger:
    def test_first_caller_wins(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        first = ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="backend",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=4,
        )
        second = ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="backend",
            claimer_id="B",
            ttl_seconds=60,
            per_role_max_in_flight=4,
        )
        assert first.granted
        assert not second.granted
        assert second.reason == "held"
        assert second.claimer_id == "A"

    def test_expired_lease_is_recovered(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=4,
            now=1000.0,
        )
        recovered = ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="B",
            ttl_seconds=60,
            per_role_max_in_flight=4,
            now=5000.0,
        )
        assert recovered.granted
        assert recovered.claimer_id == "B"

    def test_concurrency_ceiling(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="backend",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=1,
        )
        capped = ledger.try_claim(
            tracker="t",
            ticket_id="T-2",
            role="backend",
            claimer_id="B",
            ttl_seconds=60,
            per_role_max_in_flight=1,
        )
        assert not capped.granted
        assert capped.reason == "concurrency_ceiling"

    def test_release_drops_claim(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=4,
        )
        assert ledger.release(tracker="t", ticket_id="T-1", role="qa", claimer_id="A")
        # After release another claimer can take it.
        again = ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="B",
            ttl_seconds=60,
            per_role_max_in_flight=4,
        )
        assert again.granted

    def test_release_ignores_wrong_owner(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=4,
        )
        assert not ledger.release(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="OTHER",
        )

    def test_two_agent_race_exactly_one_wins(self, tmp_path: Path) -> None:
        """Threaded race against the same ticket+role yields one winner."""
        ledger = ClaimLedger(tmp_path / "claims.db")
        barrier = Barrier(8)
        results: list[ClaimOutcome] = []

        def attempt(claimer: str) -> None:
            barrier.wait()
            result = ledger.try_claim(
                tracker="t",
                ticket_id="T-1",
                role="backend",
                claimer_id=claimer,
                ttl_seconds=60,
                per_role_max_in_flight=10,
            )
            results.append(result)

        threads = [Thread(target=attempt, args=(f"c{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [r for r in results if r.granted]
        losers = [r for r in results if not r.granted]
        assert len(winners) == 1
        assert len(losers) == 7
        assert all(r.reason == "held" for r in losers)

    def test_same_database_ledgers_share_process_lock(self, tmp_path: Path) -> None:
        ledger_a = ClaimLedger(tmp_path / "claims.db")
        ledger_b = ClaimLedger(tmp_path / "nested" / ".." / "claims.db")
        assert ledger_a._lock is ledger_b._lock

    def test_attempt_counter_starts_at_zero_then_bumps(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        ledger.try_claim(
            tracker="t",
            ticket_id="T-1",
            role="qa",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=4,
        )
        assert ledger.attempt_count(tracker="t", ticket_id="T-1", role="qa") == 0
        first = ledger.bump_attempt(tracker="t", ticket_id="T-1", role="qa", claimer_id="A")
        second = ledger.bump_attempt(tracker="t", ticket_id="T-1", role="qa", claimer_id="A")
        assert first == 1
        assert second == 2
        assert ledger.attempt_count(tracker="t", ticket_id="T-1", role="qa") == 2

    def test_bump_attempt_returns_minus_one_when_unknown(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        assert (
            ledger.bump_attempt(
                tracker="t",
                ticket_id="T-X",
                role="qa",
                claimer_id="A",
            )
            == -1
        )

    def test_live_claims_excludes_expired_rows(self, tmp_path: Path) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        ledger.try_claim(
            tracker="t",
            ticket_id="alive",
            role="backend",
            claimer_id="A",
            ttl_seconds=60,
            per_role_max_in_flight=4,
            now=1000.0,
        )
        ledger.try_claim(
            tracker="t",
            ticket_id="stale",
            role="qa",
            claimer_id="B",
            ttl_seconds=1,
            per_role_max_in_flight=4,
            now=900.0,
        )
        rows = ledger.live_claims(now=1010.0)
        # Only the 1000.0+60 row is still alive at now=1010.0; the 900+1
        # claim aged out and must not appear.
        assert [r["ticket_id"] for r in rows] == ["alive"]
        assert rows[0]["lease_seconds_remaining"] > 0


# ---------------------------------------------------------------------------
# Pipeline config
# ---------------------------------------------------------------------------


class TestPipelineConfig:
    def test_from_dict_full(self) -> None:
        raw = {
            "pipeline_stages": [
                {
                    "role": "architect",
                    "claim_status": "ready",
                    "success_status": "design-approved",
                    "failure_status": "design-blocked",
                },
                {
                    "role": "backend",
                    "claim_status": "design-approved",
                    "success_status": "code-review",
                    "failure_status": "blocked",
                    "requires_prior_role": "architect",
                },
            ],
            "claim_lock_ttl_seconds": 120,
            "concurrency": {"per_role_max_in_flight": 4},
        }
        cfg = PipelineConfig.from_dict(raw)
        assert len(cfg.pipeline_stages) == 2
        assert cfg.claim_lock_ttl_seconds == 120
        assert cfg.per_role_max_in_flight == 4
        assert cfg.pipeline_stages[1].requires_prior_role == "architect"
        assert cfg.stage_for_role("backend") == cfg.pipeline_stages[1]
        assert cfg.stage_for_role("missing") is None

    def test_from_dict_defaults(self) -> None:
        cfg = PipelineConfig.from_dict({})
        assert cfg.pipeline_stages == ()
        assert cfg.claim_lock_ttl_seconds == 600
        assert cfg.per_role_max_in_flight == 1

    def test_stage_requires_explicit_keys(self) -> None:
        with pytest.raises(TrackerPipelineError):
            PipelineConfig.from_dict({"pipeline_stages": [{"role": "x"}]})

    def test_default_ledger_relpath(self) -> None:
        # Stable path lookup so operators know where the file lands.
        assert Path("state") / "tracker_claims.db" == DEFAULT_LEDGER_RELPATH


# ---------------------------------------------------------------------------
# Pipeline end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def two_stage_config() -> PipelineConfig:
    return _config(
        [
            PipelineStage(
                role="architect",
                claim_status="ready",
                success_status="design-done",
                failure_status="design-blocked",
            ),
            PipelineStage(
                role="backend",
                claim_status="design-done",
                success_status="qa",
                failure_status="blocked",
                requires_prior_role="architect",
            ),
        ],
        max_in_flight=2,
    )


class TestTrackerPipeline:
    def test_happy_path_emits_handoff_and_transitions(
        self,
        tmp_path: Path,
        two_stage_config: PipelineConfig,
    ) -> None:
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="ready")],
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=two_stage_config,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
            claimer_id="worker-A",
        )
        emitted = pipeline.tick()
        assert emitted == 1
        assert len(adapter.comments) == 1
        comment_ticket, body, comment_key = adapter.comments[0]
        assert comment_ticket == "T-1"
        assert "yaml bernstein:success" in body
        assert comment_key is not None and comment_key.endswith(":comment")
        assert len(adapter.transitions) == 1
        assert adapter.transitions[0][1] == "design-done"
        assert len(pipeline.handoffs) == 1
        handoff = pipeline.handoffs[0]
        assert handoff.outcome == "success"
        assert handoff.role == "architect"
        assert handoff.to_status == "design-done"

    def test_prior_role_gate_blocks_backend_until_architect_done(
        self,
        tmp_path: Path,
        two_stage_config: PipelineConfig,
    ) -> None:
        # Ticket is already at design-done but the body shows no prior
        # architect success block; backend stage must skip it.
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="design-done", body="bare body")],
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=two_stage_config,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        emitted = pipeline.tick()
        assert emitted == 0
        assert dispatcher.calls == []

    def test_prior_role_gate_releases_when_marker_present(
        self,
        tmp_path: Path,
        two_stage_config: PipelineConfig,
    ) -> None:
        marker_body = 'previous architect handoff\n\n```yaml bernstein:success\nrole: "architect"\n```\n'
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="design-done", body=marker_body)],
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=two_stage_config,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        pipeline.tick()
        assert any(call[2] == "backend" for call in dispatcher.calls)

    def test_prior_role_gate_uses_structured_block_not_raw_match(
        self,
        tmp_path: Path,
        two_stage_config: PipelineConfig,
    ) -> None:
        """Body mentions ``architect`` in prose only - gate must stay closed.

        Earlier revisions did a raw substring scan that matched any
        occurrence of ``role: "<name>"`` anywhere in the ticket text;
        this test pins the structured-parse behaviour so prose that
        happens to contain the literal token does not unlock the gate.
        """
        prose_only = 'architect noted that role: "architect" should review later but did not write a success block.\n'
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="design-done", body=prose_only)],
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=two_stage_config,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        pipeline.tick()
        assert dispatcher.calls == []

    def test_prior_role_gate_tolerant_of_extra_yaml_fields(
        self,
        tmp_path: Path,
        two_stage_config: PipelineConfig,
    ) -> None:
        """Extra YAML fields and quoting variations do not break the gate."""
        block_body = (
            "earlier handoff\n\n"
            + format_success_comment(
                role="architect",
                stage_attempt=2,
                idempotency_key="abc",
                summary="design approved with notes",
                prose="prose above the block",
            )
            + "\n"
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="design-done", body=block_body)],
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=two_stage_config,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        pipeline.tick()
        assert any(call[2] == "backend" for call in dispatcher.calls)

    def test_idempotent_retry_uses_same_key_for_same_attempt(
        self,
        tmp_path: Path,
    ) -> None:
        # Simulate two ticks where the first crashes between bump and
        # write by manually invoking make_idempotency_key with the
        # captured attempt number.
        cfg = _config(
            [
                PipelineStage(
                    role="qa",
                    claim_status="ready-qa",
                    success_status="done",
                    failure_status="blocked",
                )
            ]
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="ready-qa")],
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=cfg,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        pipeline.tick()
        assert len(dispatcher.calls) == 1
        first_key = dispatcher.calls[0][4]
        # A second tick on a fresh adapter run with a *new* ticket of
        # identical id and a fresh ledger would produce the same key:
        ledger2 = ClaimLedger(tmp_path / "claims2.db")
        pipeline2 = TrackerPipeline(
            config=cfg,
            trackers={
                "fake": FakeTrackerAdapter(
                    name="fake",
                    tickets=[_ticket("T-1", status="ready-qa")],
                ),
            },
            ledger=ledger2,
            dispatcher=RecordingDispatcher(),
        )
        pipeline2.tick()
        assert pipeline2.dispatcher.calls[0][4] == first_key  # type: ignore[attr-defined]

    def test_concurrency_limit_honoured(self, tmp_path: Path) -> None:
        cfg = _config(
            [
                PipelineStage(
                    role="backend",
                    claim_status="ready",
                    success_status="done",
                    failure_status="blocked",
                )
            ],
            max_in_flight=1,
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[
                _ticket("T-1", status="ready"),
                _ticket("T-2", status="ready"),
                _ticket("T-3", status="ready"),
            ],
        )
        # Hold a parallel claim so the loop bumps into the ceiling.
        ledger.try_claim(
            tracker="fake",
            ticket_id="T-OTHER",
            role="backend",
            claimer_id="WorkerX",
            ttl_seconds=60,
            per_role_max_in_flight=1,
        )
        dispatcher = RecordingDispatcher()
        pipeline = TrackerPipeline(
            config=cfg,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        emitted = pipeline.tick()
        assert emitted == 0
        assert dispatcher.calls == []

    def test_failure_outcome_writes_failure_block_and_transitions(
        self,
        tmp_path: Path,
    ) -> None:
        cfg = _config(
            [
                PipelineStage(
                    role="qa",
                    claim_status="ready-qa",
                    success_status="done",
                    failure_status="qa-blocked",
                )
            ]
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="ready-qa")],
        )
        dispatcher = RecordingDispatcher(
            plan={
                ("T-1", "qa"): DispatchOutcome(
                    success=False,
                    failure=FailurePayload(
                        reason_code="tests.red",
                        category="permanent",
                        transient=False,
                        next_action="escalate",
                        detail="3 cases fail",
                    ),
                    prose="qa report",
                )
            }
        )
        pipeline = TrackerPipeline(
            config=cfg,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        pipeline.tick()
        assert adapter.transitions[0][1] == "qa-blocked"
        block_body = adapter.comments[0][1]
        parsed = parse_failure_block(block_body)
        assert parsed is not None
        assert parsed["reason_code"] == "tests.red"
        assert parsed["next_action"] == "escalate"
        assert pipeline.handoffs[0].outcome == "failure"

    def test_transient_failure_returns_ticket_to_claim_status(
        self,
        tmp_path: Path,
    ) -> None:
        cfg = _config(
            [
                PipelineStage(
                    role="qa",
                    claim_status="ready-qa",
                    success_status="done",
                    failure_status="qa-blocked",
                )
            ]
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="ready-qa")],
        )
        dispatcher = RecordingDispatcher(
            plan={
                ("T-1", "qa"): DispatchOutcome(
                    success=False,
                    failure=FailurePayload(
                        reason_code="net.flake",
                        category="transient",
                        transient=True,
                        next_action="retry",
                    ),
                )
            }
        )
        pipeline = TrackerPipeline(
            config=cfg,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=dispatcher,
        )
        pipeline.tick()
        # Transient failure -> back to claim_status, ledger keeps the
        # claim so the same worker may retry without contention next
        # tick.
        assert adapter.transitions[0][1] == "ready-qa"

    def test_dispatcher_exception_logs_and_does_not_escape(
        self,
        tmp_path: Path,
    ) -> None:
        class BoomDispatcher:
            def dispatch(
                self,
                *,
                tracker: str,
                ticket: Ticket,
                role: str,
                stage_attempt: int,
                idempotency_key: str,
            ) -> DispatchOutcome:
                raise RuntimeError("crash")

        cfg = _config(
            [
                PipelineStage(
                    role="qa",
                    claim_status="ready-qa",
                    success_status="done",
                    failure_status="qa-blocked",
                )
            ]
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="ready-qa")],
        )
        pipeline = TrackerPipeline(
            config=cfg,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=BoomDispatcher(),
        )
        emitted = pipeline.tick()
        # The pipeline writes a synthetic failure comment for the crash.
        assert emitted == 1
        parsed = parse_failure_block(adapter.comments[0][1])
        assert parsed is not None
        assert parsed["reason_code"] == "dispatch.exception"
        assert adapter.transitions[0][1] == "qa-blocked"

    def test_handoff_log_aggregates_per_role(
        self,
        tmp_path: Path,
    ) -> None:
        handoffs = [
            StageHandoff(
                tracker="t",
                ticket_id=f"T-{i}",
                role="backend",
                from_status="ready",
                to_status="done",
                stage_attempt=1,
                outcome="success",
                idempotency_key="k",
            )
            for i in range(3)
        ]
        handoffs.append(
            StageHandoff(
                tracker="t",
                ticket_id="T-9",
                role="backend",
                from_status="ready",
                to_status="blocked",
                stage_attempt=1,
                outcome="failure",
                idempotency_key="k",
            )
        )
        counts = role_names_in_flight(handoffs)
        assert counts == {"backend": 3}

    def test_lifecycle_hook_receives_handoff_payload(
        self,
        tmp_path: Path,
    ) -> None:
        from bernstein.core.lifecycle.hooks import (
            HookRegistry,
            LifecycleContext,
            LifecycleEvent,
        )

        received: list[dict[str, Any]] = []

        def hook(ctx: LifecycleContext) -> None:
            received.append(dict(ctx.data))

        registry = HookRegistry()
        registry.register_callable(LifecycleEvent.POST_TASK, hook)

        cfg = _config(
            [
                PipelineStage(
                    role="qa",
                    claim_status="ready-qa",
                    success_status="done",
                    failure_status="qa-blocked",
                )
            ]
        )
        ledger = ClaimLedger(tmp_path / "claims.db")
        adapter = FakeTrackerAdapter(
            name="fake",
            tickets=[_ticket("T-1", status="ready-qa")],
        )
        pipeline = TrackerPipeline(
            config=cfg,
            trackers={"fake": adapter},
            ledger=ledger,
            dispatcher=RecordingDispatcher(),
            hook_registry=registry,
        )
        pipeline.tick()
        assert len(received) == 1
        payload = received[0]
        assert payload["handoff_event_name"] == "tracker_pipeline.handoff"
        assert payload["tracker"] == "fake"
        assert payload["ticket_id"] == "T-1"
        assert payload["role"] == "qa"
        assert payload["outcome"] == "success"
        assert payload["from_status"] == "ready-qa"
        assert payload["to_status"] == "done"
        assert payload["stage_attempt"] == 1
        assert isinstance(payload["idempotency_key"], str)
