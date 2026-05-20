"""Tests for the lineage chain wrapper through the A2A envelope (AC 3)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bernstein.core.interop.a2a_lineage import (
    CROSS_ORG_BOUNDARY_MARKER,
    LINEAGE_ENVELOPE_FIELD,
    LineageEnvelope,
    append_cross_org_segment,
    chain_digest,
    wrap_lineage_chain,
)
from bernstein.core.lineage.tracker_audit import TrackerActor, TrackerAuditLog

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def actor() -> TrackerActor:
    return TrackerActor(session_id="s1", role="backend", model="m")


def _seed_source(path: Path, actor: TrackerActor) -> TrackerAuditLog:
    log = TrackerAuditLog(path, hmac_key=b"sender-operator-secret")
    log.append(
        tracker_name="jira", ticket_id="PROJ-1", action="claim", actor=actor, input_prompt=b"p", output_blob=b"o"
    )
    log.append(
        tracker_name="jira", ticket_id="PROJ-1", action="comment", actor=actor, input_prompt=b"p2", output_blob=b"o2"
    )
    return log


def test_wrap_carries_signed_chain_under_envelope_field(tmp_path: Path, actor: TrackerActor) -> None:
    src = _seed_source(tmp_path / "src.jsonl", actor)
    envelope = wrap_lineage_chain(src, source_issuer="acme")
    field = envelope.to_envelope_field()
    assert LINEAGE_ENVELOPE_FIELD in field
    payload = field[LINEAGE_ENVELOPE_FIELD]
    assert payload["source_issuer"] == "acme"
    assert len(payload["entries"]) == 2
    assert payload["chain_digest"].startswith("sha256:")


def test_envelope_round_trips_through_json(tmp_path: Path, actor: TrackerActor) -> None:
    src = _seed_source(tmp_path / "src.jsonl", actor)
    envelope = wrap_lineage_chain(src, source_issuer="acme")
    wire = json.loads(json.dumps(envelope.to_envelope_field()))
    rebuilt = LineageEnvelope.from_envelope_field(wire)
    assert rebuilt.chain_digest == envelope.chain_digest
    assert chain_digest(rebuilt.entries) == envelope.chain_digest


def test_tampered_carried_chain_is_detected(tmp_path: Path, actor: TrackerActor) -> None:
    src = _seed_source(tmp_path / "src.jsonl", actor)
    envelope = wrap_lineage_chain(src, source_issuer="acme")
    payload = json.loads(json.dumps(envelope.to_payload()))
    payload["entries"][0]["ticket_id"] = "EVIL"
    with pytest.raises(ValueError, match="chain_digest mismatch"):
        LineageEnvelope.from_payload(payload)


def test_missing_envelope_field_raises(actor: TrackerActor) -> None:
    with pytest.raises(ValueError, match=LINEAGE_ENVELOPE_FIELD):
        LineageEnvelope.from_envelope_field({"some_other_field": {}})


def test_receiver_appends_boundary_marker(tmp_path: Path, actor: TrackerActor) -> None:
    src = _seed_source(tmp_path / "src.jsonl", actor)
    envelope = wrap_lineage_chain(src, source_issuer="acme")

    receiver = TrackerAuditLog(tmp_path / "recv.jsonl", hmac_key=b"receiver-operator-secret")
    receiver_actor = TrackerActor(session_id="r1", role="reviewer", model="m")
    entry = append_cross_org_segment(receiver, envelope, actor=receiver_actor, ticket_id="RECV-9")

    assert entry.tracker_name == CROSS_ORG_BOUNDARY_MARKER
    assert entry.ticket_id == "RECV-9"
    assert entry.action == "comment"
    assert entry.actor == receiver_actor
    # the boundary marker content is content-addressed; recompute the same
    # hash from the marker body to confirm the entry binds the source chain.
    from bernstein.core.lineage.tracker_audit import content_hash

    expected_marker = json.dumps(
        {
            "marker": CROSS_ORG_BOUNDARY_MARKER,
            "source_issuer": envelope.source_issuer,
            "source_chain_digest": envelope.chain_digest,
            "source_entry_count": len(envelope.entries),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert entry.input_prompt_hash == content_hash(expected_marker)


def test_receiver_chain_verifies_after_append(tmp_path: Path, actor: TrackerActor) -> None:
    src = _seed_source(tmp_path / "src.jsonl", actor)
    envelope = wrap_lineage_chain(src, source_issuer="acme")

    receiver_key = b"receiver-operator-secret"
    receiver = TrackerAuditLog(tmp_path / "recv.jsonl", hmac_key=receiver_key)
    receiver_actor = TrackerActor(session_id="r1", role="reviewer", model="m")
    append_cross_org_segment(receiver, envelope, actor=receiver_actor, ticket_id="RECV-9")

    # the receiver's existing verify path validates the boundary entry with
    # no new primitive.
    result = receiver.verify()
    assert result.ok is True
    assert result.entry_count == 1


def test_wrap_filters_by_ticket(tmp_path: Path, actor: TrackerActor) -> None:
    log = TrackerAuditLog(tmp_path / "src.jsonl", hmac_key=b"k")
    log.append(tracker_name="jira", ticket_id="A-1", action="claim", actor=actor, input_prompt=b"p", output_blob=b"o")
    log.append(tracker_name="jira", ticket_id="B-2", action="claim", actor=actor, input_prompt=b"p", output_blob=b"o")
    envelope = wrap_lineage_chain(log, source_issuer="acme", ticket_id="A-1")
    assert len(envelope.entries) == 1
    assert envelope.entries[0].ticket_id == "A-1"
