"""Cluster Prometheus metric wiring (audit-CO-1).

Asserts that the five new ``bernstein_cluster_*`` series appear on the
``/metrics`` surface after a representative cluster cycle (register +
heartbeat + steal + autoscale + admission failure).
"""

from __future__ import annotations

from prometheus_client import generate_latest

from bernstein.core.observability.prometheus import (
    record_admission_failure,
    record_heartbeat,
    record_scaling_decision,
    record_steal_attempt,
    registry,
    set_node_count,
)
from bernstein.core.protocols.cluster.cluster import NodeRegistry
from bernstein.core.protocols.cluster.cluster_autoscaler import (
    AutoscaleConfig,
    AutoscaleExecutor,
    ClusterAutoscaler,
    NoOpBackend,
    QueueSnapshot,
)
from bernstein.core.protocols.cluster.cluster_task_stealing import (
    NodeLoad,
    StealableTask,
    StealConfig,
    TaskStealingEngine,
)
from tests.unit.test_cluster import _make_config, _make_node


def _scrape() -> str:
    """Return the current ``/metrics`` payload as text."""
    return generate_latest(registry).decode("utf-8")


# ---------------------------------------------------------------------------
# Direct helper coverage - catches typos in the label vocabulary.
# ---------------------------------------------------------------------------


def test_set_node_count_renders_known_status() -> None:
    set_node_count("online", 3)
    assert 'bernstein_cluster_nodes_total{status="online"} 3.0' in _scrape()


def test_set_node_count_buckets_unknown_status() -> None:
    set_node_count("not-a-real-status", 1)
    assert 'bernstein_cluster_nodes_total{status="unknown"} 1.0' in _scrape()


def test_record_heartbeat_emits_each_outcome() -> None:
    record_heartbeat("accepted")
    record_heartbeat("rejected_token")
    record_heartbeat("rejected_unknown_node")
    out = _scrape()
    assert 'bernstein_cluster_heartbeats_total{result="accepted"}' in out
    assert 'bernstein_cluster_heartbeats_total{result="rejected_token"}' in out
    assert 'bernstein_cluster_heartbeats_total{result="rejected_unknown_node"}' in out


def test_record_steal_attempt_emits_each_outcome() -> None:
    for label in ("stolen", "cooldown", "no_victim", "rejected_version_mismatch"):
        record_steal_attempt(label)
    out = _scrape()
    for label in ("stolen", "cooldown", "no_victim", "rejected_version_mismatch"):
        assert f'bernstein_cluster_task_steals_total{{result="{label}"}}' in out


def test_record_scaling_decision_emits_action_and_backend() -> None:
    record_scaling_decision("scale_up", "kubernetes")
    record_scaling_decision("no_op", "noop")
    out = _scrape()
    assert 'bernstein_cluster_scaling_decisions_total{action="scale_up",backend="kubernetes"}' in out
    assert 'bernstein_cluster_scaling_decisions_total{action="no_op",backend="noop"}' in out


def test_record_admission_failure_emits_each_reason() -> None:
    for reason in ("invalid_token", "scope_denied", "cert_invalid"):
        record_admission_failure(reason)
    out = _scrape()
    for reason in ("invalid_token", "scope_denied", "cert_invalid"):
        assert f'bernstein_cluster_admission_failures_total{{reason="{reason}"}}' in out


# ---------------------------------------------------------------------------
# Integration coverage - wiring inside cluster.py / stealing / autoscaler.
# ---------------------------------------------------------------------------


def test_node_registry_register_updates_gauge_and_heartbeat_counter() -> None:
    reg = NodeRegistry(_make_config())
    node = reg.register(_make_node())

    out = _scrape()
    assert 'bernstein_cluster_nodes_total{status="online"}' in out

    reg.heartbeat(node.id)
    reg.heartbeat("does-not-exist")
    out = _scrape()
    assert 'bernstein_cluster_heartbeats_total{result="accepted"}' in out
    assert 'bernstein_cluster_heartbeats_total{result="rejected_unknown_node"}' in out


def test_task_stealing_records_metric() -> None:
    engine = TaskStealingEngine(StealConfig(steal_threshold=2, max_steal_batch=1, cooldown_s=0.0))
    nodes = [
        NodeLoad(node_id="thief", queued_tasks=0, available_slots=4, total_slots=4),
        NodeLoad(node_id="victim", queued_tasks=5, available_slots=0, total_slots=4),
    ]
    victim_tasks = {
        "victim": [StealableTask(task_id="t1", node_id="victim", priority=1)],
    }
    # The first call has no candidates because there's only one task on the
    # victim and the threshold is 2.  Force a SUCCESS path with extras.
    victim_tasks["victim"] = [
        StealableTask(task_id="t1", node_id="victim", priority=1),
        StealableTask(task_id="t2", node_id="victim", priority=2),
    ]
    attempt = engine.attempt_steal("thief", nodes, victim_tasks)
    assert attempt.tasks_stolen == ["t1"]
    assert 'bernstein_cluster_task_steals_total{result="stolen"}' in _scrape()


def test_autoscaler_executor_records_decision() -> None:
    autoscaler = ClusterAutoscaler(AutoscaleConfig(high_watermark=1, cooldown_s=0.0))
    backend = NoOpBackend(simulated_count=1)
    executor = AutoscaleExecutor(autoscaler, backend)

    snap = QueueSnapshot(total_queued=20, node_count=1, node_queues={"a": 20})
    decision, _result = executor.tick(snap)

    out = _scrape()
    assert decision.direction.value == "up"
    assert 'bernstein_cluster_scaling_decisions_total{action="scale_up",backend="noop"}' in out
