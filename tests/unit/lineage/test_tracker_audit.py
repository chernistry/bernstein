"""Tests for ``bernstein.core.lineage.tracker_audit``.

Coverage matrix:

* Entry shape -- schema_version + required fields land on disk.
* Chain integrity -- ``prev_entry_hash`` walks the file in order.
* Signature verify -- HMAC mismatch is detected.
* Replay determinism -- canonical bytes are stable.
* Tampering -- mutating one byte makes ``verify`` fail with the
  offending line number.
* Adapter wrapping -- success and failure paths both emit entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.lineage.tracker_audit import (
    GENESIS_PREV_HASH,
    SCHEMA_VERSION,
    AuditingTrackerAdapter,
    LineageCtx,
    TrackerActor,
    TrackerAuditEntry,
    TrackerAuditLog,
    canonicalise_entry,
    compute_entry_hash,
    compute_signature,
    content_hash,
    wrap_adapter,
)


@pytest.fixture
def hmac_key() -> bytes:
    return b"k" * 32


@pytest.fixture
def actor() -> TrackerActor:
    return TrackerActor(session_id="sess-1", role="backend", model="claude-opus-4-7")


@pytest.fixture
def log(tmp_path: Path, hmac_key: bytes) -> TrackerAuditLog:
    return TrackerAuditLog(tmp_path / "tracker_audit.jsonl", hmac_key=hmac_key)


# ---------------------------------------------------------------------------
# Entry shape + serialisation
# ---------------------------------------------------------------------------


def test_append_genesis_entry_has_zero_prev_hash(log: TrackerAuditLog, actor: TrackerActor) -> None:
    result = log.append(
        tracker_name="jira",
        ticket_id="PROJ-1",
        action="claim",
        actor=actor,
        input_prompt=b"claim",
        output_blob=b"ok",
    )
    assert result.entry.prev_entry_hash == GENESIS_PREV_HASH
    assert result.entry.schema_version == SCHEMA_VERSION
    assert result.entry.entry_hash.startswith("sha256:")
    assert result.entry.signature
    # The file is JSONL with one entry per line.
    raw = log.path.read_bytes()
    assert raw.endswith(b"\n")
    payload = json.loads(raw.strip())
    assert payload["tracker_name"] == "jira"
    assert payload["actor"]["role"] == "backend"


def test_input_and_output_blob_hashes_are_content_addressed(log: TrackerAuditLog, actor: TrackerActor) -> None:
    body = b"hello there"
    blob = b"server-response"
    result = log.append(
        tracker_name="jira",
        ticket_id="PROJ-1",
        action="comment",
        actor=actor,
        input_prompt=body,
        output_blob=blob,
    )
    assert result.entry.input_prompt_hash == content_hash(body)
    assert result.entry.output_blob_hash == content_hash(blob)


def test_unknown_action_rejected_at_append(log: TrackerAuditLog, actor: TrackerActor) -> None:
    with pytest.raises(ValueError, match="unknown tracker-audit action"):
        log.append(
            tracker_name="jira",
            ticket_id="PROJ-1",
            action="explode",  # type: ignore[arg-type]
            actor=actor,
            input_prompt=b"",
            output_blob=b"",
        )


def test_entry_post_init_rejects_bad_hash_prefix() -> None:
    actor = TrackerActor(session_id="s", role="r", model="m")
    with pytest.raises(ValueError, match="entry_hash must start with 'sha256:'"):
        TrackerAuditEntry(
            schema_version=SCHEMA_VERSION,
            id="x",
            ts_ns=0,
            prev_entry_hash=GENESIS_PREV_HASH,
            entry_hash="md5:abc",
            tracker_name="jira",
            ticket_id="T-1",
            etag_before=None,
            etag_after=None,
            action="claim",
            actor=actor,
            input_prompt_hash="sha256:" + "0" * 64,
            output_blob_hash="sha256:" + "0" * 64,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            idempotency_key=None,
            lifecycle_event_id=None,
            signature="",
        )


# ---------------------------------------------------------------------------
# Chain integrity
# ---------------------------------------------------------------------------


def test_chain_walks_in_insertion_order(log: TrackerAuditLog, actor: TrackerActor) -> None:
    r1 = log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    r2 = log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="comment",
        actor=actor,
        input_prompt=b"b",
        output_blob=b"b",
    )
    r3 = log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="transition",
        actor=actor,
        input_prompt=b"c",
        output_blob=b"c",
    )
    assert r2.entry.prev_entry_hash == r1.entry.entry_hash
    assert r3.entry.prev_entry_hash == r2.entry.entry_hash


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def test_verify_clean_log_returns_ok(log: TrackerAuditLog, actor: TrackerActor) -> None:
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="comment",
        actor=actor,
        input_prompt=b"b",
        output_blob=b"b",
    )
    result = log.verify()
    assert result.ok
    assert result.entry_count == 2
    assert result.failures == []


def test_verify_missing_log_returns_ok_with_zero_entries(tmp_path: Path, hmac_key: bytes) -> None:
    log = TrackerAuditLog(tmp_path / "nonexistent.jsonl", hmac_key=hmac_key)
    result = log.verify()
    assert result.ok is True
    assert result.entry_count == 0


def test_signature_mismatch_detected_with_wrong_key(log: TrackerAuditLog, actor: TrackerActor, tmp_path: Path) -> None:
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    # Re-read with a different key. The signature check must fail.
    other = TrackerAuditLog(log.path, hmac_key=b"wrong-key")
    result = other.verify()
    assert not result.ok
    assert any("signature mismatch" in f for f in result.failures)


# ---------------------------------------------------------------------------
# Tampering
# ---------------------------------------------------------------------------


def test_tampering_byte_flip_detected(log: TrackerAuditLog, actor: TrackerActor) -> None:
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="comment",
        actor=actor,
        input_prompt=b"original-body",
        output_blob=b"server",
    )

    # Flip one byte in line 2 of the JSONL by rewriting the file.
    raw_lines = log.path.read_bytes().splitlines()
    payload = json.loads(raw_lines[1].decode("utf-8"))
    payload["ticket_id"] = "T-2"  # mutate
    raw_lines[1] = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    log.path.write_bytes(b"\n".join(raw_lines) + b"\n")

    result = log.verify()
    assert not result.ok
    # The failure should mention line 2 -- the tampered one.
    assert any("line 2" in f for f in result.failures)


def test_tampering_invalid_json_detected(log: TrackerAuditLog, actor: TrackerActor) -> None:
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    log.path.write_bytes(b"not json\n")
    result = log.verify()
    assert not result.ok
    assert any("invalid JSON" in f for f in result.failures)


# ---------------------------------------------------------------------------
# Replay determinism
# ---------------------------------------------------------------------------


def test_canonical_bytes_are_deterministic(actor: TrackerActor) -> None:
    base_kwargs: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": "abc",
        "ts_ns": 1234,
        "prev_entry_hash": GENESIS_PREV_HASH,
        "entry_hash": "sha256:" + "0" * 64,
        "tracker_name": "jira",
        "ticket_id": "T-1",
        "etag_before": None,
        "etag_after": "etag-v2",
        "action": "claim",
        "actor": actor,
        "input_prompt_hash": "sha256:" + "1" * 64,
        "output_blob_hash": "sha256:" + "2" * 64,
        "cost_usd": 0.0125,
        "tokens_in": 100,
        "tokens_out": 50,
        "idempotency_key": "idem-1",
        "lifecycle_event_id": None,
        "signature": "",
    }
    e1 = TrackerAuditEntry(**base_kwargs)
    e2 = TrackerAuditEntry(**base_kwargs)
    assert canonicalise_entry(e1) == canonicalise_entry(e2)
    assert compute_entry_hash(e1) == compute_entry_hash(e2)


def test_filter_by_tracker_and_ticket(log: TrackerAuditLog, actor: TrackerActor) -> None:
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    log.append(
        tracker_name="linear",
        ticket_id="L-9",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    log.append(
        tracker_name="jira",
        ticket_id="T-2",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    jira_only = log.filter(tracker_name="jira")
    assert {e.ticket_id for e in jira_only} == {"T-1", "T-2"}

    t1_only = log.filter(ticket_id="T-1")
    assert len(t1_only) == 1
    assert t1_only[0].tracker_name == "jira"


def test_export_bundle_writes_matching_lines(log: TrackerAuditLog, actor: TrackerActor, tmp_path: Path) -> None:
    log.append(
        tracker_name="jira",
        ticket_id="T-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    log.append(
        tracker_name="linear",
        ticket_id="L-1",
        action="claim",
        actor=actor,
        input_prompt=b"a",
        output_blob=b"a",
    )
    out = tmp_path / "bundle.jsonl"
    n = log.export_bundle(out, tracker_name="jira")
    assert n == 1
    payload = json.loads(out.read_bytes().strip())
    assert payload["tracker_name"] == "jira"


# ---------------------------------------------------------------------------
# Adapter wrapping
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Minimal stand-in for an :class:`AbstractTrackerAdapter`."""

    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def claim_ticket(self, ticket_id: str, agent_id: str, *, etag: str | None = None) -> Any:
        self.calls.append(("claim_ticket", (ticket_id, agent_id, etag)))
        if self._fail:
            raise RuntimeError("backend exploded")
        from bernstein.core.trackers.contract import ClaimResult

        return ClaimResult(claimed=True, ticket_id=ticket_id, agent_id=agent_id, etag="etag-1")

    def add_comment(self, ticket_id: str, body: str, *, idempotency_key: str | None = None) -> Any:
        self.calls.append(("add_comment", (ticket_id, body, idempotency_key)))
        from bernstein.core.trackers.contract import CommentResult

        return CommentResult(comment_id="c1", ticket_id=ticket_id)

    def transition(
        self,
        ticket_id: str,
        status_id: str,
        *,
        idempotency_key: str | None = None,
        etag: str | None = None,
    ) -> Any:
        self.calls.append(("transition", (ticket_id, status_id)))
        from bernstein.core.trackers.contract import TransitionResult

        return TransitionResult(ticket_id=ticket_id, new_status=status_id, etag="etag-2")

    def attach_blob(
        self,
        ticket_id: str,
        blob: bytes,
        mime: str,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        self.calls.append(("attach_blob", (ticket_id, len(blob), mime)))
        from bernstein.core.trackers.contract import AttachResult

        return AttachResult(attachment_id="att-1", ticket_id=ticket_id)


def test_wrap_adapter_emits_audit_on_success(log: TrackerAuditLog, actor: TrackerActor) -> None:
    ctx = LineageCtx(log=log, actor=actor, cost_usd=0.01, tokens_in=10, tokens_out=20)
    wrapped = wrap_adapter(_FakeAdapter(), ctx)
    assert isinstance(wrapped, AuditingTrackerAdapter)
    wrapped.claim_ticket("T-1", "agent:claude-1", etag="etag-0")
    wrapped.add_comment("T-1", "hello", idempotency_key="idem-1")
    wrapped.transition("T-1", "done")
    wrapped.attach_blob("T-1", b"binary-blob", "application/octet-stream")
    entries = log.read()
    assert [e.action for e in entries] == ["claim", "comment", "transition", "attach"]
    assert entries[0].actor.role == "backend"
    assert entries[0].cost_usd == 0.01
    # etag pulled from the returned ClaimResult
    assert entries[0].etag_after == "etag-1"


def test_wrap_adapter_emits_failure_entry_and_reraises(log: TrackerAuditLog, actor: TrackerActor) -> None:
    ctx = LineageCtx(log=log, actor=actor)
    wrapped = wrap_adapter(_FakeAdapter(fail=True), ctx)
    with pytest.raises(RuntimeError, match="backend exploded"):
        wrapped.claim_ticket("T-1", "agent:claude-1")
    entries = log.read()
    assert len(entries) == 1
    assert entries[0].action == "fail"
    assert entries[0].failure_category == "RuntimeError"
    assert "backend exploded" in (entries[0].failure_detail or "")


def test_signature_helper_round_trips(actor: TrackerActor, hmac_key: bytes) -> None:
    entry = TrackerAuditEntry(
        schema_version=SCHEMA_VERSION,
        id="x",
        ts_ns=42,
        prev_entry_hash=GENESIS_PREV_HASH,
        entry_hash="sha256:" + "0" * 64,
        tracker_name="jira",
        ticket_id="T-1",
        etag_before=None,
        etag_after=None,
        action="claim",
        actor=actor,
        input_prompt_hash="sha256:" + "1" * 64,
        output_blob_hash="sha256:" + "2" * 64,
        cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        idempotency_key=None,
        lifecycle_event_id=None,
        signature="",
    )
    sig = compute_signature(entry, hmac_key)
    assert sig == compute_signature(entry, hmac_key)  # deterministic
    assert sig != compute_signature(entry, b"different-key")
