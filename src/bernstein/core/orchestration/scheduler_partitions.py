"""Channel-scoped scheduler partitions for chat-driven dispatch.

A *partition* is a string label (typically ``<platform>:<channel_id>``)
that pins both a task and a worker. The scheduler refuses to dispatch a
task to a worker whose partition differs from the task's partition, and
the chat drivers refuse to resolve a pending approval whose partition
does not match the channel the click arrived in.

The helper is intentionally tiny: it owns the canonical partition string
format, an alias table for operators who want to group channels, the
equality check, and a structured :class:`PartitionEvent` dataclass that
chat drivers persist verbatim into the HMAC-chained audit log.

Two consumers today: the Discord driver (issue #1795) and the Slack
driver (issue #1794). Both call :func:`partition_id_for_channel` so the
on-disk partition labels stay consistent across drivers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "ChannelPartitionMap",
    "PartitionEvent",
    "PartitionViolationError",
    "partition_id_for_channel",
]


def partition_id_for_channel(platform: str, channel_id: str) -> str:
    """Return the canonical ``<platform>:<channel_id>`` partition id.

    Both inputs are stripped and lower-cased so trailing whitespace from
    a copy-pasted operator config or a case-shift in the platform name
    do not silently produce two partitions for the same logical channel.

    Args:
        platform: Driver name (``"discord"``, ``"slack"``, ``"telegram"``).
        channel_id: Platform-native channel id, as a string.

    Returns:
        A canonical partition string, e.g. ``"discord:42"``.

    Raises:
        ValueError: If either input is empty after stripping.
    """
    plat = platform.strip().lower()
    cid = channel_id.strip().lower()
    if not plat:
        raise ValueError("platform must be non-empty.")
    if not cid:
        raise ValueError("channel_id must be non-empty.")
    return f"{plat}:{cid}"


class PartitionViolationError(RuntimeError):
    """Raised when a task and a worker are on different partitions.

    The error carries both partition ids so the audit-chain entry the
    caller writes can record exactly which partition was expected and
    which was offered. Callers should log to the chain before raising.

    Attributes:
        expected: The partition the task or pending approval is bound to.
        actual: The partition the request arrived from.
    """

    def __init__(self, *, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"partition mismatch: expected {expected!r}, got {actual!r}",
        )


@dataclass(slots=True, frozen=True)
class PartitionEvent:
    """Audit-chain payload describing a partition-fence decision.

    Used by chat drivers to record the partition that gated an approval
    or a dispatch into the HMAC-chained log. Kept as a plain dataclass
    so :meth:`to_dict` produces a JSON-friendly payload that the audit
    log can sort, hash, and replay byte-identically.

    Attributes:
        partition_id: Canonical partition label.
        task_id: Optional task id this decision was about. Empty when
            the event is purely about a worker / approval scope.
        worker_id: Optional worker id this decision was about.
        platform: Driver name, kept for replay convenience.
        channel_id: Platform-native channel id, kept for replay convenience.
    """

    partition_id: str
    platform: str = ""
    channel_id: str = ""
    task_id: str = ""
    worker_id: str = ""

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-friendly dict suitable for the audit chain."""
        return {
            "partition_id": self.partition_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "platform": self.platform,
            "channel_id": self.channel_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PartitionEvent:
        """Rehydrate from a raw dict; missing optional fields default to empty."""
        return cls(
            partition_id=str(payload.get("partition_id", "")),
            platform=str(payload.get("platform", "")),
            channel_id=str(payload.get("channel_id", "")),
            task_id=str(payload.get("task_id", "")),
            worker_id=str(payload.get("worker_id", "")),
        )


class ChannelPartitionMap:
    """Resolves ``(platform, channel_id)`` to a canonical partition id.

    By default the mapping is the identity:
    ``partition_for("discord", "42") == "discord:42"``. Operators can
    install aliases so several channels collapse into one partition
    (e.g. multiple #ops channels share an ``"ops"`` worker pool).

    The map is in-memory and process-local; callers persisting state
    typically just record the canonical partition string and rebuild the
    alias table from config on startup.
    """

    def __init__(self) -> None:
        self._aliases: dict[tuple[str, str], str] = {}

    def alias(self, *, platform: str, channel_id: str, partition_id: str) -> None:
        """Bind ``(platform, channel_id)`` to a custom ``partition_id``.

        The alias does *not* normalise ``partition_id`` -- operators may
        use any identifier they like (e.g. ``"ops"`` or ``"prod-emea"``).
        Lookup keys are normalised the same way as
        :func:`partition_id_for_channel`.
        """
        plat = platform.strip().lower()
        cid = channel_id.strip().lower()
        if not plat or not cid:
            raise ValueError("platform and channel_id must both be non-empty.")
        self._aliases[(plat, cid)] = partition_id

    def partition_for(self, platform: str, channel_id: str) -> str:
        """Return the canonical or aliased partition id for ``(platform, channel_id)``."""
        plat = platform.strip().lower()
        cid = channel_id.strip().lower()
        key = (plat, cid)
        if key in self._aliases:
            return self._aliases[key]
        return partition_id_for_channel(platform, channel_id)

    @staticmethod
    def enforce(*, expected: str, actual: str) -> None:
        """Raise :class:`PartitionViolationError` if partitions differ.

        Drivers call this on the approval-resolution path: ``expected``
        is the partition the pending approval was registered against,
        ``actual`` is the partition the operator's click arrived from.
        """
        if expected != actual:
            raise PartitionViolationError(expected=expected, actual=actual)
