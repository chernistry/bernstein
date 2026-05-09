"""custom-audit-sink — mirror bernstein audit events to MQTT.

Worked example of the ``on_audit_event`` hookspec. Implements a
fail-soft sink: a broker outage logs at WARNING but never raises into
the orchestrator's main loop.
"""

from __future__ import annotations

from custom_audit_sink._sink import (
    DEFAULT_TOPIC,
    MqttAuditSink,
    SinkConfig,
)

__all__ = [
    "DEFAULT_TOPIC",
    "MqttAuditSink",
    "SinkConfig",
]
