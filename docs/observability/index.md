# Cluster observability

Bernstein exposes Prometheus metrics and HMAC-chained audit events for
every cluster operation.  Wire your Prometheus scraper at the task
server's `/metrics` endpoint and ship the audit JSONL to your SIEM.

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
audit log.  The chain (`AuditLog.verify()`) covers these new event
types alongside task and security events:

| Event type | Resource | Key fields |
| --- | --- | --- |
| `CLUSTER_NODE_REGISTERED` | `cluster_node` | `node_id`, `role`, `registered_at`, `initial_capacity` |
| `CLUSTER_NODE_LEFT` | `cluster_node` | `node_id`, `reason` (`graceful` / `timeout` / `unregistered`) |
| `CLUSTER_NODE_CORDONED` | `cluster_node` | `node_id` |
| `CLUSTER_NODE_DRAINED` | `cluster_node` | `node_id` |
| `CLUSTER_TASK_STOLEN` | `cluster_task` | `task_id`, `from_node`, `to_node`, `queue_depth_delta` |
| `CLUSTER_SCALE_DECISION` | `cluster_scale` | `action`, `target_count`, `backend`, `dry_run` |

## Grafana

Import `docs/observability/cluster-grafana.json` into Grafana for a
single-pane view: the node-status gauge plus the four counter rates.
Point it at any Prometheus datasource that scrapes Bernstein.
