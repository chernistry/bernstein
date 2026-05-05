# Cluster observability

Bernstein exposes Prometheus metrics and HMAC-chained audit events for
every cluster operation. Wire your Prometheus scraper at the task
server's `/metrics` endpoint and ship the audit JSONL to your SIEM.

## Why it exists

Before this batch, cluster mode emitted no Prometheus signal and no
audit event for register / heartbeat / steal / scale operations. That
made production debugging impossible: the audit log wasn't there to
replay, and the metrics weren't there to graph. This change adds five
metrics and six event types, both rolled into the existing
infrastructure (no new chain, no new endpoint).

## How to scrape

```yaml
# prometheus.yml
scrape_configs:
  - job_name: bernstein-cluster
    metrics_path: /metrics
    static_configs:
      - targets: ['central.bernstein.example.com:8052']
```

Authenticate the scraper using the same Bearer token mechanism the rest
of the task server uses (see [Security and identity](../operations/security-and-identity.md)).

## Prometheus metrics

| Metric | Type | Labels | Example PromQL |
| --- | --- | --- | --- |
| `bernstein_cluster_nodes_total` | gauge | `status` (`online`, `ready`, `degraded`, `cordoned`, `draining`, `offline`) | `bernstein_cluster_nodes_total{status="online"}` |
| `bernstein_cluster_heartbeats_total` | counter | `result` (`accepted`, `rejected_token`, `rejected_unknown_node`) | `sum by (result) (rate(bernstein_cluster_heartbeats_total[5m]))` |
| `bernstein_cluster_task_steals_total` | counter | `result` (`stolen`, `cooldown`, `no_victim`, `rejected_version_mismatch`) | `rate(bernstein_cluster_task_steals_total{result="stolen"}[5m])` |
| `bernstein_cluster_scaling_decisions_total` | counter | `action` (`scale_up`, `scale_down`, `no_op`), `backend` (`noop`, `kubernetes`) | `sum by (action) (increase(bernstein_cluster_scaling_decisions_total[1h]))` |
| `bernstein_cluster_admission_failures_total` | counter | `reason` (`invalid_token`, `scope_denied`, `cert_invalid`) | `sum by (reason) (rate(bernstein_cluster_admission_failures_total[5m]))` |

Label values are bucketed against a closed set; anything outside the
allowed vocabulary is collapsed to `unknown` to keep series cardinality
bounded.

## Audit events

Every cluster mutation is recorded through the existing HMAC-chained
audit log. The chain (`AuditLog.verify()`) covers these new event
types alongside task and security events:

| Event type | Resource | Key fields |
| --- | --- | --- |
| `CLUSTER_NODE_REGISTERED` | `cluster_node` | `node_id`, `role`, `registered_at`, `initial_capacity` |
| `CLUSTER_NODE_LEFT` | `cluster_node` | `node_id`, `reason` (`graceful` / `timeout` / `unregistered`) |
| `CLUSTER_NODE_CORDONED` | `cluster_node` | `node_id` |
| `CLUSTER_NODE_DRAINED` | `cluster_node` | `node_id` |
| `CLUSTER_TASK_STOLEN` | `cluster_task` | `task_id`, `from_node`, `to_node`, `queue_depth_delta` |
| `CLUSTER_SCALE_DECISION` | `cluster_scale` | `action`, `target_count`, `backend`, `dry_run` |

To verify the chain after a run:

```bash
bernstein audit verify
```

## Grafana

Import `docs/observability/cluster-grafana.json` into Grafana for a
single-pane view: the node-status gauge plus the four counter rates.
Point it at any Prometheus datasource that scrapes Bernstein.

## Configuration

| Knob | Default | Controls |
|---|--:|---|
| `observability.prometheus.enabled` | `true` | Master switch for `/metrics`. |
| `audit.cluster_events.enabled` | `true` | Emit cluster events into the chain. |
| `observability.label_cardinality_cap` | `unknown`-bucket | Outside-vocabulary label values get collapsed. |

## Limitations

- No distributed tracing yet. OTel spans for cluster ops are tracked
  as a separate ticket; counters + gauge are the floor.
- The audit log lives on the central node only. Workers do not keep
  a local copy of cluster events in v1 (audit volume tradeoff).
- The Grafana JSON is one example dashboard, not a product. Customise
  freely.
- No alert rules ship with this — recommended alerting will land
  after we observe the metrics in real deployments.

## Related

- Source: `src/bernstein/core/observability/prometheus.py`,
  `src/bernstein/core/security/audit_log.py`
- [Cluster mTLS setup](../cluster/mtls-setup.md)
- [Cluster deployment patterns](../cluster/deployment-patterns.md)
- [Operations / Observability overview](../operations/observability-overview.md)
- PR #1021, ticket `2026-05-05-feat-cluster-observability.md`
