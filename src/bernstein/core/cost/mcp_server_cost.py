"""Per-server MCP cost meter (issue #1673).

The hardened MCP client treats every upstream server as billable: a remote
tool call may incur a metered cost (the server's own LLM spend, an egress
fee, a per-call price). Bernstein accumulates that spend per server per task
so the orchestrator can attribute, cap, and report it alongside its own LLM
spend.

This module is a thin accumulation layer that sits in front of the existing
:class:`~bernstein.core.cost.spend_ledger.SpendLedger`. It keeps an
in-memory rolling total per ``(task_id, server_name)`` pair and, when a
ledger is wired in, flushes each metered call into it tagged with the server
name so the regular cost rollups (``totals_by("task")`` etc.) include MCP
spend.

The meter never raises on a missing ledger; it degrades to in-memory
accounting so a misconfigured cost subsystem cannot take the orchestrator
down (the same brittleness contract the rest of the hardened client follows).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bernstein.core.cost.spend_ledger import SpendLedger

logger = logging.getLogger(__name__)

# Synthetic model label used when flushing MCP spend into the LLM-shaped
# ledger. Keeps MCP rows distinguishable from real model spend in rollups.
MCP_LEDGER_MODEL = "mcp-server"


@dataclass(frozen=True)
class ServerCostRecord:
    """A single metered MCP tool call.

    Attributes:
        task_id: Task the call was made on behalf of.
        server_name: Remote MCP server that billed the call.
        tool_name: Tool invoked.
        cost_usd: Cost attributed to the call (clamped to >= 0).
        calls: Number of underlying calls this record represents (>= 1).
    """

    task_id: str
    server_name: str
    tool_name: str
    cost_usd: float
    calls: int = 1


@dataclass
class MCPServerCostMeter:
    """Accumulate MCP spend per server per task.

    The meter is process-local and thread-safe. Wire a
    :class:`~bernstein.core.cost.spend_ledger.SpendLedger` in via ``ledger``
    to also flush each metered call into the shared ledger; leave it ``None``
    for pure in-memory accounting (tests, dry-runs).

    Args:
        ledger: Optional shared spend ledger to flush metered calls into.
        feature_label: Feature label stamped onto flushed ledger rows.
    """

    ledger: SpendLedger | None = None
    feature_label: str = "mcp-client"

    # task_id -> server_name -> accumulated USD
    _by_task_server: dict[str, dict[str, float]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(float)),
        init=False,
        repr=False,
    )
    # task_id -> server_name -> call count
    _calls_by_task_server: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int)),
        init=False,
        repr=False,
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(
        self,
        *,
        task_id: str,
        server_name: str,
        tool_name: str,
        cost_usd: float,
    ) -> ServerCostRecord:
        """Record one metered MCP tool call.

        Negative costs are clamped to zero so a misreporting server cannot
        corrupt the rolling totals. When a ledger is wired in, the call is
        also flushed there tagged with the server name; a ledger failure is
        logged and swallowed so accounting never takes the client down.

        Args:
            task_id: Task the call belongs to (``""`` -> ``"unknown"``).
            server_name: Remote server that billed the call.
            tool_name: Tool that was invoked.
            cost_usd: Cost to attribute. Clamped to ``>= 0``.

        Returns:
            The :class:`ServerCostRecord` that was accumulated.
        """
        cost = max(0.0, cost_usd)
        task = task_id or "unknown"
        with self._lock:
            self._by_task_server[task][server_name] += cost
            self._calls_by_task_server[task][server_name] += 1

        self._flush_to_ledger(task_id=task, server_name=server_name, cost_usd=cost)
        return ServerCostRecord(
            task_id=task,
            server_name=server_name,
            tool_name=tool_name,
            cost_usd=cost,
        )

    def cost_for(self, task_id: str, server_name: str) -> float:
        """Return accumulated USD for one ``(task, server)`` pair."""
        task = task_id or "unknown"
        with self._lock:
            return self._by_task_server.get(task, {}).get(server_name, 0.0)

    def task_total(self, task_id: str) -> float:
        """Return total MCP spend across all servers for ``task_id``."""
        task = task_id or "unknown"
        with self._lock:
            return sum(self._by_task_server.get(task, {}).values())

    def server_breakdown(self, task_id: str) -> dict[str, float]:
        """Return a ``{server_name: usd}`` map for ``task_id`` (copy)."""
        task = task_id or "unknown"
        with self._lock:
            return dict(self._by_task_server.get(task, {}))

    def call_count(self, task_id: str, server_name: str) -> int:
        """Return number of metered calls for one ``(task, server)`` pair."""
        task = task_id or "unknown"
        with self._lock:
            return self._calls_by_task_server.get(task, {}).get(server_name, 0)

    def _flush_to_ledger(self, *, task_id: str, server_name: str, cost_usd: float) -> None:
        """Flush one metered call into the shared spend ledger, if wired."""
        if self.ledger is None or cost_usd <= 0.0:
            return
        try:
            from bernstein.core.cost.spend_ledger import CallTags

            self.ledger.record(
                tags=CallTags(
                    task_id=task_id,
                    feature_label=self.feature_label,
                    extra={"mcp_server": server_name},
                ),
                model=MCP_LEDGER_MODEL,
                cost_usd=cost_usd,
            )
        except Exception as exc:
            logger.warning(
                "MCPServerCostMeter: failed to flush %.6f USD for server '%s' into ledger: %s",
                cost_usd,
                server_name,
                exc,
            )
