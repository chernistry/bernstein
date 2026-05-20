"""In-flight tool-call tracking with cancellation and partial-result preservation.

The MCP spec lets a client cancel an in-flight request by sending a
``notifications/cancelled`` notification carrying the target ``requestId``.
For a long-running tool call the server should stop the work *and* preserve
whatever the handler produced before the cancel so the client is not left
with a bare error.

This module provides the small amount of state that makes that possible on
the streamable HTTP transport:

  * :class:`InFlightCall` - one cancellable tool call. It owns an
    :class:`asyncio.Task`, a ``partial`` buffer the handler can append to as
    it streams output, and a cancel flag.
  * :class:`InFlightRegistry` - a per-transport map of ``requestId`` to
    :class:`InFlightCall`, so an incoming cancel can find and stop the right
    task and read its partial buffer.

The handler signals streaming progress by appending to the call's ``partial``
list. On cancel the registry returns the accumulated partial output rather
than discarding it, so the response carries ``cancelled: true`` together with
``partial``: the chunks gathered before the stop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InFlightCall:
    """A single cancellable in-flight tool call.

    Attributes:
        request_id: The JSON-RPC ``id`` of the originating ``tools/call``.
        tool: The tool name being executed.
        task: The asyncio task running the tool body, set once scheduled.
        partial: Output chunks the handler has produced so far. Preserved and
            returned to the client if the call is cancelled mid-flight.
        cancelled: Set ``True`` when a cancel notification targeted this call.
    """

    request_id: int | str
    tool: str
    task: asyncio.Task[str] | None = None
    partial: list[str] = field(default_factory=list[str])
    cancelled: bool = False

    def append_partial(self, chunk: str) -> None:
        """Record a streamed output chunk for partial-result preservation."""
        self.partial.append(chunk)

    def partial_text(self) -> str:
        """Return the accumulated partial output as a single string."""
        return "".join(self.partial)


class InFlightRegistry:
    """Tracks cancellable tool calls keyed by JSON-RPC request id.

    The registry is per-transport (created in the transport ``__init__``) and
    guarded by its own lock so concurrent POSTs (a tool call and its cancel)
    do not race on the shared map.
    """

    def __init__(self) -> None:
        self._calls: dict[int | str, InFlightCall] = {}
        self._lock = asyncio.Lock()

    async def register(self, request_id: int | str, tool: str) -> InFlightCall:
        """Create and store an :class:`InFlightCall` for ``request_id``."""
        call = InFlightCall(request_id=request_id, tool=tool)
        async with self._lock:
            self._calls[request_id] = call
        return call

    async def attach_task(self, request_id: int | str, task: asyncio.Task[str]) -> None:
        """Bind the running task to a previously registered call."""
        async with self._lock:
            call = self._calls.get(request_id)
            if call is not None:
                call.task = task

    async def get(self, request_id: int | str) -> InFlightCall | None:
        """Return the in-flight call for ``request_id``, if still tracked."""
        async with self._lock:
            return self._calls.get(request_id)

    async def discard(self, request_id: int | str) -> None:
        """Stop tracking ``request_id`` once its call has settled."""
        async with self._lock:
            self._calls.pop(request_id, None)

    async def cancel(self, request_id: int | str) -> InFlightCall | None:
        """Cancel the in-flight call for ``request_id``.

        Marks the call cancelled and cancels its task (if scheduled). The
        call is returned so the caller can read the preserved ``partial``
        buffer; it is *not* discarded here because the tool-call handler is
        responsible for emitting the partial result and then discarding.

        Returns:
            The cancelled :class:`InFlightCall`, or ``None`` when no call with
            that id is in flight (an already-finished or unknown id).
        """
        async with self._lock:
            call = self._calls.get(request_id)
            if call is None:
                return None
            call.cancelled = True
            if call.task is not None and not call.task.done():
                call.task.cancel()
            return call


def cancelled_envelope(call: InFlightCall, meter_dict: dict[str, Any]) -> dict[str, Any]:
    """Build the ``tools/call`` result body for a cancelled call.

    Carries ``cancelled: true`` and the preserved ``partial`` output so the
    client keeps the work done before the stop. ``isError`` is left ``False``:
    a cancel is a client-initiated stop, not a tool failure.

    Args:
        call: The cancelled in-flight call holding the partial buffer.
        meter_dict: The finalised per-call meter record to attach.

    Returns:
        A JSON-RPC ``tools/call`` result dict.
    """
    return {
        "content": [{"type": "text", "text": call.partial_text()}],
        "cancelled": True,
        "partial": call.partial,
        "_meter": meter_dict,
    }
