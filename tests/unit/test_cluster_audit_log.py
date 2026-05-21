"""HMAC-chained audit events for cluster operations (audit-CO-1).

Mirrors the lineage-trail regression test: assert that the chain stays
intact when the new cluster event types are interleaved with normal task
events.  Also covers the per-helper happy-path: each ``record_*`` call
appends exactly one entry of the right type.
"""

from __future__ import annotations

from pathlib import Path

from bernstein.core.protocols.cluster import cluster_audit
from bernstein.core.security.audit import AuditLog
from bernstein.core.tasks.lifecycle import set_audit_log

# ---------------------------------------------------------------------------
# Direct helpers - confirms each event type is routed through the AuditLog
# and produces exactly one entry with the expected fields.
# ---------------------------------------------------------------------------


def _wire(tmp_path: Path) -> AuditLog:
    log = AuditLog(tmp_path / "audit", key=b"test-key-cluster")
    set_audit_log(log)
    return log


def _teardown() -> None:
    # The singleton has no public unsetter - clear it via the module attribute.
    from bernstein.core.tasks import lifecycle

    lifecycle._audit_log = None  # pyright: ignore[reportPrivateUsage]


def test_record_node_registered_appends_event(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        cluster_audit.record_node_registered("node-A", role="worker", registered_at=1234.5, initial_capacity=8)
        events = log.query()
        assert len(events) == 1
        assert events[0].event_type == "CLUSTER_NODE_REGISTERED"
        assert events[0].details["initial_capacity"] == 8
        assert events[0].details["role"] == "worker"
    finally:
        _teardown()


def test_record_node_left_buckets_unknown_reason(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        cluster_audit.record_node_left("node-A", reason="meteor-strike")
        events = log.query()
        assert events[-1].event_type == "CLUSTER_NODE_LEFT"
        assert events[-1].details["reason"] == "unknown"
    finally:
        _teardown()


def test_record_node_left_accepts_known_reasons(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        for reason in ("graceful", "timeout", "unregistered"):
            cluster_audit.record_node_left("node-A", reason=reason)
        events = log.query(event_type="CLUSTER_NODE_LEFT")
        assert {e.details["reason"] for e in events} == {"graceful", "timeout", "unregistered"}
    finally:
        _teardown()


def test_record_node_cordoned_and_drained(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        cluster_audit.record_node_cordoned("node-X")
        cluster_audit.record_node_drained("node-X")
        types = [e.event_type for e in log.query()]
        assert "CLUSTER_NODE_CORDONED" in types
        assert "CLUSTER_NODE_DRAINED" in types
    finally:
        _teardown()


def test_record_task_stolen(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        cluster_audit.record_task_stolen("task-42", from_node="A", to_node="B", queue_depth_delta=3)
        events = log.query()
        assert events[-1].event_type == "CLUSTER_TASK_STOLEN"
        assert events[-1].details == {
            "task_id": "task-42",
            "from_node": "A",
            "to_node": "B",
            "queue_depth_delta": 3,
        }
    finally:
        _teardown()


def test_record_scale_decision(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        cluster_audit.record_scale_decision(action="scale_up", target_count=4, backend="noop", dry_run=True)
        events = log.query()
        assert events[-1].event_type == "CLUSTER_SCALE_DECISION"
        assert events[-1].details["dry_run"] is True
        assert events[-1].details["target_count"] == 4
    finally:
        _teardown()


# ---------------------------------------------------------------------------
# Regression - chain integrity must survive cluster events interleaved with
# normal entries.  Mirrors test_hmac_chain_intact_with_lineage_records from
# the lineage trail PR.
# ---------------------------------------------------------------------------


def test_hmac_chain_intact_with_cluster_events(tmp_path: Path) -> None:
    log = _wire(tmp_path)
    try:
        # Mix existing event types with the five new cluster types.
        log.log("task.created", "system", "task", "t1", {"foo": 1})
        cluster_audit.record_node_registered("node-A", role="worker", registered_at=1.0, initial_capacity=4)
        cluster_audit.record_node_cordoned("node-A")
        log.log("task.completed", "agent-1", "task", "t1", {})
        cluster_audit.record_node_drained("node-A")
        cluster_audit.record_task_stolen("t9", from_node="node-A", to_node="node-B", queue_depth_delta=1)
        cluster_audit.record_scale_decision(action="scale_down", target_count=1, backend="kubernetes", dry_run=False)
        cluster_audit.record_node_left("node-A", reason="graceful")

        ok, errors = log.verify()
        assert ok, errors
        # All five cluster event types should appear at least once.
        types = {e.event_type for e in log.query()}
        assert "CLUSTER_NODE_REGISTERED" in types
        assert "CLUSTER_NODE_CORDONED" in types
        assert "CLUSTER_NODE_DRAINED" in types
        assert "CLUSTER_TASK_STOLEN" in types
        assert "CLUSTER_SCALE_DECISION" in types
        assert "CLUSTER_NODE_LEFT" in types
    finally:
        _teardown()


def test_no_audit_log_wired_is_silent(tmp_path: Path) -> None:
    """Without a wired audit log, every helper is a no-op (no exception)."""
    from bernstein.core.tasks import lifecycle

    lifecycle._audit_log = None  # pyright: ignore[reportPrivateUsage]
    cluster_audit.record_node_registered("node-A", role="worker", registered_at=1.0, initial_capacity=1)
    cluster_audit.record_node_cordoned("node-A")
    cluster_audit.record_node_drained("node-A")
    cluster_audit.record_node_left("node-A", reason="graceful")
    cluster_audit.record_task_stolen("t1", from_node="A", to_node="B", queue_depth_delta=0)
    cluster_audit.record_scale_decision(action="no_op", target_count=1, backend="noop", dry_run=True)
    # No file was written.
    assert not (tmp_path / "audit").exists()
