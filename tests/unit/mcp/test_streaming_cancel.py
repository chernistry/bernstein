"""Tests for streaming tool-call cancel + partial-result preservation (#1674).

Covers:

  * the :class:`InFlightRegistry` lifecycle (register, attach, cancel, discard);
  * the cancelled-result envelope shape (``cancelled``, ``partial``, ``_meter``);
  * an end-to-end cancel through the streamable HTTP transport: a slow
    ``tools/call`` cancelled by a concurrent ``notifications/cancelled`` returns
    the preserved partial output rather than an error;
  * cancelling an unknown id is a no-op;
  * the HTTP capabilities advertise cancellation with partial results.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from bernstein.mcp.remote_transport import (
    _CAPABILITIES,
    RemoteMCPConfig,
    StreamableHTTPTransport,
)
from bernstein.mcp.streaming import (
    InFlightCall,
    InFlightRegistry,
    cancelled_envelope,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BERNSTEIN_MCP_TOKEN", raising=False)
    monkeypatch.delenv("BERNSTEIN_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BERNSTEIN_MCP_COST_METER", raising=False)


@pytest.fixture
def transport(_clean_env: None) -> StreamableHTTPTransport:
    cfg = RemoteMCPConfig(host="127.0.0.1", auth_type="none")
    return StreamableHTTPTransport(config=cfg, server_url="https://test:8052")


def _request(method: str, params: dict, req_id: int) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "method": method, "id": req_id, "params": params}).encode()


def _notification(method: str, params: dict) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


async def test_registry_register_get_discard() -> None:
    reg = InFlightRegistry()
    call = await reg.register(7, "bernstein_status")
    assert call.request_id == 7
    assert await reg.get(7) is call
    await reg.discard(7)
    assert await reg.get(7) is None


async def test_registry_cancel_marks_and_cancels_task() -> None:
    reg = InFlightRegistry()
    await reg.register(1, "bernstein_run")

    async def _slow() -> str:
        await asyncio.sleep(10)
        return "{}"

    task: asyncio.Task[str] = asyncio.ensure_future(_slow())
    await reg.attach_task(1, task)
    call = await reg.cancel(1)
    assert call is not None
    assert call.cancelled is True
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_registry_cancel_unknown_id_is_noop() -> None:
    reg = InFlightRegistry()
    assert await reg.cancel("nope") is None


# ---------------------------------------------------------------------------
# Cancelled envelope
# ---------------------------------------------------------------------------


def test_cancelled_envelope_preserves_partial() -> None:
    call = InFlightCall(request_id=3, tool="bernstein_run")
    call.append_partial('{"chunk": 1}')
    call.append_partial('{"chunk": 2}')
    env = cancelled_envelope(call, {"tool": "bernstein_run", "ok": False})
    assert env["cancelled"] is True
    assert env["partial"] == ['{"chunk": 1}', '{"chunk": 2}']
    assert env.get("isError") is None
    assert env["_meter"]["tool"] == "bernstein_run"


# ---------------------------------------------------------------------------
# End-to-end cancel through the transport
# ---------------------------------------------------------------------------


async def test_tools_call_cancelled_returns_partial(
    transport: StreamableHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def _slow_execute(name: str, arguments: dict) -> str:
        started.set()
        await asyncio.sleep(10)
        return json.dumps({"unreached": True})

    monkeypatch.setattr(transport, "_execute_tool", _slow_execute)

    call_body = _request("tools/call", {"name": "bernstein_status", "arguments": {}}, req_id=42)
    call_fut = asyncio.ensure_future(transport.handle_request("POST", "/mcp", {}, call_body))

    # Wait until the slow tool is actually running, then cancel it.
    await asyncio.wait_for(started.wait(), timeout=2.0)
    cancel_body = _notification("notifications/cancelled", {"requestId": 42})
    status, _, _ = await transport.handle_request("POST", "/mcp", {}, cancel_body)
    assert status == 204  # notification -> no body

    status, _, resp_body = await asyncio.wait_for(call_fut, timeout=2.0)
    assert status == 200
    result = json.loads(resp_body)["result"]
    assert result["cancelled"] is True
    # The seeded "running" partial chunk is preserved.
    assert any("running" in chunk for chunk in result["partial"])
    assert result.get("isError") is not True


async def test_tools_call_not_cancelled_completes_normally(
    transport: StreamableHTTPTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fast_execute(name: str, arguments: dict) -> str:
        return json.dumps({"total": 1})

    monkeypatch.setattr(transport, "_execute_tool", _fast_execute)
    body = _request("tools/call", {"name": "bernstein_status", "arguments": {}}, req_id=5)
    status, _, resp_body = await transport.handle_request("POST", "/mcp", {}, body)
    assert status == 200
    result = json.loads(resp_body)["result"]
    assert "cancelled" not in result
    # The id is no longer tracked once the call settles.
    assert await transport._inflight.get(5) is None


# ---------------------------------------------------------------------------
# Capabilities advertise cancellation
# ---------------------------------------------------------------------------


def test_capabilities_advertise_cancellation() -> None:
    assert _CAPABILITIES["experimental"]["cancellation"]["partialResults"] is True
