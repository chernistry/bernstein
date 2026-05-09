"""Unit tests for the custom-audit-sink example plugin."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from custom_audit_sink import DEFAULT_TOPIC, MqttAuditSink, SinkConfig


class _RecordingPublisher:
    """Capture ``(topic, body)`` pairs the sink would publish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, topic: str, body: str) -> None:
        self.calls.append((topic, body))


class TestSinkConfig:
    """Configuration is read from env at SinkConfig.from_env() time."""

    def test_defaults_disable_sink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("BERNSTEIN_AUDIT_MQTT_URL", "BERNSTEIN_AUDIT_MQTT_TOPIC", "BERNSTEIN_AUDIT_MQTT_ENABLED"):
            monkeypatch.delenv(key, raising=False)
        config = SinkConfig.from_env()
        assert config.url == ""
        assert config.topic == DEFAULT_TOPIC
        assert config.enabled is False

    def test_env_drives_url_topic_and_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BERNSTEIN_AUDIT_MQTT_URL", "mqtt://siem.local:1883")
        monkeypatch.setenv("BERNSTEIN_AUDIT_MQTT_TOPIC", "ops/bernstein/audit")
        monkeypatch.setenv("BERNSTEIN_AUDIT_MQTT_ENABLED", "1")
        config = SinkConfig.from_env()
        assert config.url == "mqtt://siem.local:1883"
        assert config.topic == "ops/bernstein/audit"
        assert config.enabled is True


class TestSinkPublish:
    """``on_audit_event`` hook routes payloads to the publisher."""

    def test_dormant_sink_does_not_publish(self) -> None:
        publisher = _RecordingPublisher()
        sink = MqttAuditSink(
            config=SinkConfig(url="", topic=DEFAULT_TOPIC, enabled=False),
            publish_fn=publisher,
        )
        sink.on_audit_event(
            event_type="task.completed",
            actor="qa-1",
            payload={"task_id": "t1"},
        )
        assert publisher.calls == [], "dormant sink must not publish anything"

    def test_active_sink_publishes_envelope(self) -> None:
        publisher = _RecordingPublisher()
        sink = MqttAuditSink(
            config=SinkConfig(url="mqtt://x:1883", topic="t/audit", enabled=True),
            publish_fn=publisher,
        )
        payload: dict[str, Any] = {"task_id": "kf-1", "duration_s": 12.5}
        sink.on_audit_event(
            event_type="task.completed",
            actor="qa-1",
            payload=payload,
        )
        assert len(publisher.calls) == 1
        topic, body = publisher.calls[0]
        assert topic == "t/audit"
        envelope = json.loads(body)
        assert envelope["event_type"] == "task.completed"
        assert envelope["actor"] == "qa-1"
        assert envelope["payload"] == payload

    def test_publisher_exception_is_swallowed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A broker outage must NOT escape into the orchestrator loop."""

        def _explode(_topic: str, _body: str) -> None:
            raise RuntimeError("broker offline")

        sink = MqttAuditSink(
            config=SinkConfig(url="mqtt://x:1883", topic="t/audit", enabled=True),
            publish_fn=_explode,
        )
        with caplog.at_level(logging.WARNING):
            # Must NOT raise.
            sink.on_audit_event(event_type="task.completed", actor="x", payload={})
        # The warning surfaces so operators can see the broker outage.
        assert any("publish" in rec.message for rec in caplog.records)


class TestPluginRegistration:
    """The plugin advertises the canonical hook target.

    bernstein's plugin manager keys hook dispatch on the method name, so
    the test asserts the class still exposes ``on_audit_event`` after
    refactors. Class-level metadata (``hook_target``) is a belt-and-
    suspenders check for catalogue-style listings.
    """

    def test_plugin_class_advertises_audit_event_hook(self) -> None:
        assert MqttAuditSink.hook_target == "on_audit_event"
        assert callable(getattr(MqttAuditSink, "on_audit_event", None))

    def test_plugin_class_has_stable_name(self) -> None:
        assert MqttAuditSink.plugin_name == "custom-audit-sink"
