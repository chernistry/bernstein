"""Tests for the hardened MCP client (issue #1673).

Covers each acceptance-criteria path with a fake MCP server:

* AC1 capability-card validation -> ``MCPCapabilityMissing`` on mismatch.
* AC2 retry-with-continuation on a streamed-tool-call drop (resume from a
  checkpoint token, or full retry with an idempotency key).
* AC3 streamed-output cancellation with partial output preserved.
* AC4 per-server cost-meter accumulation wired into ``core/cost``.
* AC5 schema-violation containment (invalid JSON / missing fields) marking
  the server degraded for the rest of the task.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from bernstein.core.cost.mcp_server_cost import MCPServerCostMeter
from bernstein.core.protocols.mcp.mcp_client import (
    MCPCapabilityMissing,
    MCPClientManager,
    MCPClientSession,
    MCPSchemaViolation,
    MCPStreamDropped,
    MCPToolNotFoundError,
    RemoteServerConfig,
    RemoteTool,
    StreamChunk,
    StreamedToolCall,
)
from bernstein.core.protocols.mcp.mcp_metrics import MCPMetricsCollector

_FAKE_REQUEST = httpx.Request("POST", "https://fake")


# ---------------------------------------------------------------------------
# Fake MCP server
# ---------------------------------------------------------------------------


class FakeMCPServer:
    """Scriptable in-memory MCP server for client tests.

    Each ``handle`` call inspects the JSON-RPC method and returns a canned
    ``httpx.Response``. Behaviour is configured per instance so individual
    tests can simulate malformed payloads, auth state, and tool catalogues.
    """

    def __init__(
        self,
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_result: dict[str, Any] | None = None,
        raw_body: str | None = None,
        non_object_body: bool = False,
    ) -> None:
        self.tools = tools if tools is not None else [{"name": "echo", "description": "Echo", "inputSchema": {}}]
        self.tool_result = (
            tool_result
            if tool_result is not None
            else {
                "content": [{"type": "text", "text": "ok"}],
                "isError": False,
            }
        )
        self.raw_body = raw_body
        self.non_object_body = non_object_body
        self.calls: list[str] = []

    def _envelope(self, request_id: int, result: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": request_id, "result": result},
            headers={"content-type": "application/json", "mcp-session-id": "sess-fake"},
            request=_FAKE_REQUEST,
        )

    async def handle(self, url: str, *, json: Any, headers: Any, **kwargs: Any) -> httpx.Response:
        method = json.get("method", "")
        self.calls.append(method)
        request_id = json.get("id", 1)
        if method == "initialize":
            return self._envelope(request_id, {"serverInfo": {"name": "fake"}})
        if method == "notifications/initialized":
            return httpx.Response(200, headers={}, request=_FAKE_REQUEST)
        if method == "tools/list":
            return self._envelope(request_id, {"tools": self.tools})
        if method == "tools/call":
            if self.raw_body is not None:
                return httpx.Response(
                    200,
                    content=self.raw_body,
                    headers={"content-type": "application/json"},
                    request=_FAKE_REQUEST,
                )
            if self.non_object_body:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": request_id, "result": ["not", "an", "object"]},
                    headers={"content-type": "application/json"},
                    request=_FAKE_REQUEST,
                )
            return self._envelope(request_id, self.tool_result)
        return httpx.Response(200, json={}, headers={}, request=_FAKE_REQUEST)


def _patched_client(server: FakeMCPServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``httpx.AsyncClient`` so POSTs hit ``server.handle``."""

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def post(self, url: str, *, json: Any, headers: Any, **kwargs: Any) -> httpx.Response:
            return await server.handle(url, json=json, headers=headers, **kwargs)

    monkeypatch.setattr("bernstein.core.protocols.mcp.mcp_client.httpx.AsyncClient", _Client)


@pytest.fixture
def config() -> RemoteServerConfig:
    return RemoteServerConfig(name="fake", url="https://fake/mcp", retry_limit=1)


# ---------------------------------------------------------------------------
# AC1: capability-card validation
# ---------------------------------------------------------------------------


class TestCapabilityValidation:
    @pytest.mark.asyncio
    async def test_known_tool_passes(self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeMCPServer(tools=[{"name": "echo", "description": "", "inputSchema": {}}])
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        await session.connect()
        result = await session.call_tool("echo", {"x": 1})
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_undeclared_tool_raises_capability_missing(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeMCPServer(tools=[{"name": "echo", "description": "", "inputSchema": {}}])
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        await session.connect()
        with pytest.raises(MCPCapabilityMissing, match="capability card"):
            await session.call_tool("undeclared", {})
        # No tools/call should have reached the server.
        assert "tools/call" not in server.calls

    @pytest.mark.asyncio
    async def test_capability_missing_is_tool_not_found_subclass(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeMCPServer(tools=[{"name": "echo", "description": "", "inputSchema": {}}])
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        await session.connect()
        with pytest.raises(MCPToolNotFoundError):
            await session.call_tool("undeclared", {})

    @pytest.mark.asyncio
    async def test_validation_can_be_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = RemoteServerConfig(name="fake", url="https://fake/mcp", retry_limit=1, validate_capabilities=False)
        server = FakeMCPServer(tools=[])  # empty manifest
        _patched_client(server, monkeypatch)
        session = MCPClientSession(cfg)
        await session.connect()
        # Empty manifest + validation off -> call dispatches.
        result = await session.call_tool("anything", {})
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_manifest_digest_is_stable(self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeMCPServer(tools=[{"name": "echo", "description": "", "inputSchema": {}}])
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        await session.connect()
        digest = session.manifest_digest
        assert len(digest) == 64  # sha256 hex
        await session.list_tools()
        assert session.manifest_digest == digest  # unchanged manifest -> same digest


# ---------------------------------------------------------------------------
# AC2: retry-with-continuation
# ---------------------------------------------------------------------------


class _Stream:
    """Records the (resume_token, idempotency_key) of each attempt."""

    def __init__(self, scripts: list[list[StreamChunk]]) -> None:
        self._scripts = scripts
        self.attempts: list[tuple[str | None, str]] = []

    def factory(self, resume: str | None, idem: str) -> AsyncIterator[StreamChunk]:
        attempt_index = len(self.attempts)
        self.attempts.append((resume, idem))
        chunks = self._scripts[min(attempt_index, len(self._scripts) - 1)]

        async def _gen() -> AsyncIterator[StreamChunk]:
            for c in chunks:
                yield c

        return _gen()


@pytest_asyncio.fixture
async def connected_session(config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch) -> MCPClientSession:
    server = FakeMCPServer(tools=[{"name": "stream", "description": "", "inputSchema": {}}])
    _patched_client(server, monkeypatch)
    session = MCPClientSession(config)
    await session.connect()
    return session


class TestRetryWithContinuation:
    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self, connected_session: MCPClientSession) -> None:
        stream = _Stream(
            [
                # attempt 0: emit a checkpoint then drop
                [StreamChunk(text="part-one ", checkpoint_token="cp-1"), StreamChunk(dropped=True)],
                # attempt 1 (resumed): finish
                [StreamChunk(text="part-two", final=True)],
            ]
        )
        result = await connected_session.call_tool_streaming("stream", {}, stream_factory=stream.factory)
        assert result.content == "part-one part-two"
        # Second attempt resumed from the checkpoint token.
        assert stream.attempts[0][0] is None
        assert stream.attempts[1][0] == "cp-1"

    @pytest.mark.asyncio
    async def test_full_retry_with_idempotency_key(self, connected_session: MCPClientSession) -> None:
        stream = _Stream(
            [
                # attempt 0: no checkpoint, just a drop
                [StreamChunk(text="partial"), StreamChunk(dropped=True)],
                # attempt 1: full replay completes
                [StreamChunk(text="done", final=True)],
            ]
        )
        result = await connected_session.call_tool_streaming("stream", {}, stream_factory=stream.factory)
        assert result.content == "partialdone"
        # No checkpoint -> resume token stays None; idempotency key is stable.
        assert stream.attempts[1][0] is None
        assert stream.attempts[0][1] == stream.attempts[1][1]

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_and_degrades(self, connected_session: MCPClientSession) -> None:
        cfg = RemoteServerConfig(name="fake", url="https://fake/mcp", retry_limit=1, max_continuation_retries=1)
        connected_session._config = cfg  # tighten retries for the test
        stream = _Stream([[StreamChunk(dropped=True)]])  # always drops
        with pytest.raises(MCPStreamDropped):
            await connected_session.call_tool_streaming("stream", {}, stream_factory=stream.factory)
        assert connected_session.is_degraded
        # initial attempt + 1 continuation
        assert len(stream.attempts) == 2

    @pytest.mark.asyncio
    async def test_stream_without_final_is_treated_as_drop(self, connected_session: MCPClientSession) -> None:
        stream = _Stream(
            [
                [StreamChunk(text="a")],  # ends without final -> drop
                [StreamChunk(text="b", final=True)],
            ]
        )
        result = await connected_session.call_tool_streaming("stream", {}, stream_factory=stream.factory)
        assert result.content == "ab"


# ---------------------------------------------------------------------------
# AC3: streamed-output cancellation
# ---------------------------------------------------------------------------


class TestStreamCancellation:
    @pytest.mark.asyncio
    async def test_cancel_preserves_partial(self, connected_session: MCPClientSession) -> None:
        handle = StreamedToolCall(server_name="fake", tool_name="stream")

        def factory(resume: str | None, idem: str) -> AsyncIterator[StreamChunk]:
            async def _gen() -> AsyncIterator[StreamChunk]:
                yield StreamChunk(text="first")
                handle.cancel()  # cancel mid-stream
                yield StreamChunk(text="second-should-be-dropped", final=True)

            return _gen()

        result = await connected_session.call_tool_streaming("stream", {}, stream_factory=factory, handle=handle)
        assert result.metadata["cancelled"] is True
        assert result.content == "first"
        assert handle.partial_content == "first"
        assert "second" not in result.content

    @pytest.mark.asyncio
    async def test_cancel_flag_idempotent(self) -> None:
        handle = StreamedToolCall(server_name="s", tool_name="t")
        handle.cancel()
        handle.cancel()
        assert handle.cancelled is True
        assert handle.is_cancel_requested is True


# ---------------------------------------------------------------------------
# AC4: per-server cost-meter
# ---------------------------------------------------------------------------


class TestCostMeter:
    @pytest.mark.asyncio
    async def test_cost_accumulates_per_server_per_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        meter = MCPServerCostMeter()
        manager = MCPClientManager(cost_meter=meter, task_id="task-1")
        server = FakeMCPServer(tools=[{"name": "echo", "description": "", "inputSchema": {}}])
        _patched_client(server, monkeypatch)
        await manager.connect(RemoteServerConfig(name="fake", url="https://fake/mcp", retry_limit=1))

        await manager.call_tool("fake", "echo", {}, cost_usd=0.02)
        await manager.call_tool("fake", "echo", {}, cost_usd=0.03)

        assert manager.server_cost("fake") == pytest.approx(0.05)
        assert manager.task_cost() == pytest.approx(0.05)
        assert meter.call_count("task-1", "fake") == 2

    def test_negative_cost_clamped(self) -> None:
        meter = MCPServerCostMeter()
        rec = meter.record(task_id="t", server_name="s", tool_name="x", cost_usd=-5.0)
        assert rec.cost_usd == 0.0
        assert meter.task_total("t") == 0.0

    def test_flush_to_ledger(self, tmp_path: Any) -> None:
        from bernstein.core.cost.spend_ledger import SpendLedger

        ledger = SpendLedger(path=tmp_path / "ledger.jsonl", run_id="run-x")
        meter = MCPServerCostMeter(ledger=ledger)
        meter.record(task_id="task-1", server_name="github", tool_name="search", cost_usd=0.10)
        assert ledger.status().spent_usd == pytest.approx(0.10)
        assert ledger.totals_by("task").get("task-1") == pytest.approx(0.10)

    def test_server_breakdown(self) -> None:
        meter = MCPServerCostMeter()
        meter.record(task_id="t", server_name="a", tool_name="x", cost_usd=0.01)
        meter.record(task_id="t", server_name="b", tool_name="y", cost_usd=0.02)
        breakdown = meter.server_breakdown("t")
        assert breakdown == {"a": pytest.approx(0.01), "b": pytest.approx(0.02)}


# ---------------------------------------------------------------------------
# AC5: schema-violation containment
# ---------------------------------------------------------------------------


class TestSchemaViolationContainment:
    @pytest.mark.asyncio
    async def test_invalid_json_marks_degraded(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeMCPServer(
            tools=[{"name": "echo", "description": "", "inputSchema": {}}],
            raw_body="this is not json",
        )
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        await session.connect()
        with pytest.raises(MCPSchemaViolation, match="invalid JSON"):
            await session.call_tool("echo", {})
        assert session.is_degraded
        assert "invalid JSON" in session.degraded_reason

    @pytest.mark.asyncio
    async def test_missing_fields_marks_degraded(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeMCPServer(
            tools=[{"name": "echo", "description": "", "inputSchema": {}}],
            non_object_body=True,
        )
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        await session.connect()
        with pytest.raises(MCPSchemaViolation):
            await session.call_tool("echo", {})
        assert session.is_degraded

    @pytest.mark.asyncio
    async def test_malformed_tool_entry_in_manifest(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = FakeMCPServer(tools=[{"description": "missing name"}])
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config)
        with pytest.raises(MCPSchemaViolation):
            await session.connect()
        assert session.is_degraded

    @pytest.mark.asyncio
    async def test_degraded_surfaces_via_metrics(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        metrics = MCPMetricsCollector()
        server = FakeMCPServer(
            tools=[{"name": "echo", "description": "", "inputSchema": {}}],
            raw_body="nope",
        )
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config, metrics=metrics)
        await session.connect()
        with pytest.raises(MCPSchemaViolation):
            await session.call_tool("echo", {})
        summary = metrics.summary("fake")
        assert summary is not None
        # The malformed call was recorded as an error on the metrics tracker.
        assert summary["error_count"] >= 1

    @pytest.mark.asyncio
    async def test_manager_lists_degraded_servers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = MCPClientManager()
        server = FakeMCPServer(
            tools=[{"name": "echo", "description": "", "inputSchema": {}}],
            raw_body="bad",
        )
        _patched_client(server, monkeypatch)
        await manager.connect(RemoteServerConfig(name="fake", url="https://fake/mcp", retry_limit=1))
        with pytest.raises(MCPSchemaViolation):
            await manager.call_tool("fake", "echo", {})
        assert manager.degraded_servers() == ["fake"]


# ---------------------------------------------------------------------------
# Telemetry on the happy path
# ---------------------------------------------------------------------------


class TestTelemetry:
    @pytest.mark.asyncio
    async def test_successful_call_records_metrics(
        self, config: RemoteServerConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        metrics = MCPMetricsCollector()
        server = FakeMCPServer(tools=[{"name": "echo", "description": "", "inputSchema": {}}])
        _patched_client(server, monkeypatch)
        session = MCPClientSession(config, metrics=metrics)
        await session.connect()
        await session.call_tool("echo", {})
        summary = metrics.summary("fake")
        assert summary is not None
        assert summary["total_calls"] == 1
        assert summary["error_count"] == 0

    def test_remote_tool_unchanged(self) -> None:
        # Guard against accidental signature drift of the public dataclass.
        tool = RemoteTool(name="t", description="d", server_name="s")
        assert tool.input_schema == {}
