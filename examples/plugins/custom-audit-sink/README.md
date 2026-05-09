# custom-audit-sink

example bernstein plugin: mirrors audit events to an `mqtt://` topic
so a downstream siem (splunk, elastic, datadog, custom) can ingest
them.

implements the `on_audit_event` hookspec which lives in
`src/bernstein/plugins/hookspecs.py`. the hook runs in the background
(`@hookspec(background=True)`) so a slow broker cannot stall the
orchestrator's main tick loop.

## install

```bash
pip install -e examples/plugins/custom-audit-sink
```

bernstein discovers the plugin via the `bernstein.plugins` entry-point
group declared in `pyproject.toml`.

## what it does

every time an audit-relevant event fires inside bernstein
(`task.completed`, `agent.spawned`, `vault.read`, ...) the plugin
serialises the event as a single-line json blob and publishes it to
the configured mqtt topic.

example payload:

```json
{
  "ts": "2026-05-09T22:01:13Z",
  "event_type": "task.completed",
  "actor": "qa-19f2",
  "payload": {"task_id": "kf-7-slice-1", "duration_s": 42}
}
```

## configuration

```bash
export BERNSTEIN_AUDIT_MQTT_URL="mqtt://siem.internal:1883"
export BERNSTEIN_AUDIT_MQTT_TOPIC="bernstein/audit"
# optional, off by default — leaves the plugin dormant when unset
export BERNSTEIN_AUDIT_MQTT_ENABLED=1
```

when `BERNSTEIN_AUDIT_MQTT_ENABLED` is unset, the plugin loads but
does nothing: hooks fire, the publish path no-ops, no broker
connection is opened. this lets the plugin ship installed-by-default
in a downstream environment without forcing every operator to also
provision an mqtt broker.

## what's stubbed vs real

the public mqtt connection is stubbed for the example; replace
`_publish` with `paho.mqtt.client.Client.publish(...)` (or whichever
client you use) to ship to a real broker. the test suite injects a
fake sink to verify the hookimpl plumbing without needing a live
broker.

## test

```bash
cd examples/plugins/custom-audit-sink
pytest tests/ -q
```
