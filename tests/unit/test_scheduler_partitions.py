"""Unit tests for the channel-scoped scheduler partition helper.

A *partition* is a string label (typically ``<platform>:<channel_id>``)
that pins both a task and a worker. The scheduler refuses to dispatch a
task to a worker whose partition differs from the task's partition, and
the chat driver refuses to resolve a pending approval whose partition
does not match the channel the click arrived in.

The helper is intentionally tiny: it owns the canonical partition string
format, the lookup, the equality check, and a structured ``PartitionEvent``
dataclass that callers can persist verbatim into the audit chain.
"""

from __future__ import annotations

import pytest

from bernstein.core.orchestration.scheduler_partitions import (
    ChannelPartitionMap,
    PartitionEvent,
    PartitionViolationError,
    partition_id_for_channel,
)


def test_partition_id_for_channel_is_platform_scoped() -> None:
    """Two different platforms with the same channel id must not collide."""
    assert partition_id_for_channel("discord", "42") == "discord:42"
    assert partition_id_for_channel("slack", "42") == "slack:42"
    assert partition_id_for_channel("discord", "42") != partition_id_for_channel("slack", "42")


def test_partition_id_normalises_whitespace_and_case() -> None:
    """Inputs with stray whitespace or case differences must collapse."""
    assert partition_id_for_channel("Discord", " 42 ") == "discord:42"
    assert partition_id_for_channel(" SLACK", "C42") == "slack:c42"


def test_partition_id_rejects_empty_inputs() -> None:
    """Empty platform or channel ids must raise eagerly."""
    with pytest.raises(ValueError):
        partition_id_for_channel("", "42")
    with pytest.raises(ValueError):
        partition_id_for_channel("discord", "")


def test_partition_map_returns_canonical_id() -> None:
    """``ChannelPartitionMap.partition_for`` returns the canonical id."""
    pmap = ChannelPartitionMap()
    assert pmap.partition_for("discord", "100") == "discord:100"
    assert pmap.partition_for("discord", "200") == "discord:200"


def test_partition_map_supports_aliases() -> None:
    """Operators can group multiple channels under one partition explicitly."""
    pmap = ChannelPartitionMap()
    pmap.alias(platform="discord", channel_id="100", partition_id="ops")
    pmap.alias(platform="discord", channel_id="101", partition_id="ops")
    # The 'ops' alias collapses both channels into the same partition.
    assert pmap.partition_for("discord", "100") == "ops"
    assert pmap.partition_for("discord", "101") == "ops"
    # A channel without an alias still maps to its canonical id.
    assert pmap.partition_for("discord", "200") == "discord:200"


def test_partition_map_enforce_matches_returns_ok() -> None:
    """``enforce`` is a no-op when partitions match."""
    pmap = ChannelPartitionMap()
    pmap.enforce(expected="discord:42", actual="discord:42")


def test_partition_map_enforce_raises_on_mismatch() -> None:
    """``enforce`` raises ``PartitionViolationError`` when partitions differ."""
    pmap = ChannelPartitionMap()
    with pytest.raises(PartitionViolationError) as excinfo:
        pmap.enforce(expected="discord:42", actual="discord:99")
    err = excinfo.value
    assert err.expected == "discord:42"
    assert err.actual == "discord:99"


def test_partition_event_round_trips_via_dict() -> None:
    """The ``PartitionEvent`` dataclass must serialise to a plain dict for audit logs."""
    event = PartitionEvent(
        partition_id="discord:42",
        task_id="t-7",
        worker_id="w-3",
        platform="discord",
        channel_id="42",
    )
    payload = event.to_dict()
    assert payload == {
        "partition_id": "discord:42",
        "task_id": "t-7",
        "worker_id": "w-3",
        "platform": "discord",
        "channel_id": "42",
    }
    # Round trip back through ``from_dict`` reproduces the original event.
    rehydrated = PartitionEvent.from_dict(payload)
    assert rehydrated == event


def test_partition_event_from_dict_tolerates_missing_optional_fields() -> None:
    """Auditing emits a minimum-fields payload; ``from_dict`` must accept it."""
    event = PartitionEvent.from_dict(
        {
            "partition_id": "slack:C42",
            "platform": "slack",
            "channel_id": "C42",
        },
    )
    assert event.partition_id == "slack:C42"
    assert event.task_id == ""
    assert event.worker_id == ""
