"""Tests for the A2A protocol handler + value objects (``a2a/a2a.py``).

The federation layer is covered by ``tests/unit/test_a2a_federation.py``;
this module targets the handler and its value types:

* ``A2ATaskStatus`` <-> Bernstein status round-trip mapping (both helpers
  + the unknown-status fallbacks).
* ``A2AMessage`` / ``A2AArtifact`` / ``A2ATask`` ``to_dict`` contracts.
* ``AgentCard`` validate / from_dict / to_dict / json_schema, including
  every malformed-input ``ValueError`` branch.
* ``A2AHandler`` task lifecycle: create, link (+ reverse index), lookup
  by A2A id and by Bernstein id, status sync, artifact attach, listing
  with sender filter, message receive + list, and the ``KeyError``
  paths for unknown task IDs.
* ``send_message`` happy path + error propagation via an injected fake
  async client (no real network).

Deterministic; the one network method is exercised with a stub client.
"""

from __future__ import annotations

from typing import Any

import pytest

from bernstein.core.protocols.a2a.a2a import (
    A2AArtifact,
    A2AHandler,
    A2AMessage,
    A2ATask,
    A2ATaskStatus,
    AgentCard,
)

# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


class TestStatusMapping:
    @pytest.mark.parametrize(
        ("bernstein", "expected"),
        [
            ("open", A2ATaskStatus.SUBMITTED),
            ("claimed", A2ATaskStatus.WORKING),
            ("in_progress", A2ATaskStatus.WORKING),
            ("blocked", A2ATaskStatus.INPUT_REQUIRED),
            ("done", A2ATaskStatus.COMPLETED),
            ("failed", A2ATaskStatus.FAILED),
            ("cancelled", A2ATaskStatus.CANCELED),
        ],
    )
    def test_a2a_status_for(self, bernstein: str, expected: A2ATaskStatus) -> None:
        assert A2AHandler.a2a_status_for(bernstein) == expected

    def test_a2a_status_unknown_defaults_submitted(self) -> None:
        assert A2AHandler.a2a_status_for("???") == A2ATaskStatus.SUBMITTED

    @pytest.mark.parametrize(
        ("a2a_status", "expected"),
        [
            (A2ATaskStatus.SUBMITTED, "open"),
            (A2ATaskStatus.WORKING, "in_progress"),
            (A2ATaskStatus.INPUT_REQUIRED, "blocked"),
            (A2ATaskStatus.COMPLETED, "done"),
            (A2ATaskStatus.FAILED, "failed"),
            (A2ATaskStatus.CANCELED, "cancelled"),
        ],
    )
    def test_bernstein_status_for(self, a2a_status: A2ATaskStatus, expected: str) -> None:
        assert A2AHandler.bernstein_status_for(a2a_status) == expected


# ---------------------------------------------------------------------------
# Value object serialisation
# ---------------------------------------------------------------------------


class TestValueObjects:
    def test_message_to_dict(self) -> None:
        msg = A2AMessage(id="m1", sender="a", recipient="b", content="hi", task_id="t1")
        d = msg.to_dict()
        assert d["id"] == "m1"
        assert d["direction"] == "inbound"
        assert d["delivered"] is False
        assert d["external_endpoint"] is None

    def test_artifact_to_dict(self) -> None:
        art = A2AArtifact(name="out.txt", content_type="text/markdown", data="x")
        d = art.to_dict()
        assert d["name"] == "out.txt"
        assert d["content_type"] == "text/markdown"
        assert d["data"] == "x"

    def test_task_to_dict_includes_artifacts(self) -> None:
        task = A2ATask(id="t1", sender="agent", message="go")
        task.artifacts.append(A2AArtifact(name="a"))
        d = task.to_dict()
        assert d["id"] == "t1"
        assert d["status"] == "submitted"
        assert len(d["artifacts"]) == 1
        assert d["artifacts"][0]["name"] == "a"


# ---------------------------------------------------------------------------
# AgentCard
# ---------------------------------------------------------------------------


class TestAgentCard:
    def test_to_dict_round_trip(self) -> None:
        card = AgentCard(name="agent", description="does things", capabilities=["x", "y"])
        rebuilt = AgentCard.from_dict(card.to_dict())
        assert rebuilt == card

    def test_from_dict_defaults(self) -> None:
        card = AgentCard.from_dict({"name": "a", "description": "d"})
        assert card.capabilities == []
        assert card.protocol_version == "0.1"
        assert card.provider == "bernstein"

    def test_to_dict_copies_capabilities(self) -> None:
        caps = ["x"]
        card = AgentCard(name="a", description="d", capabilities=caps)
        d = card.to_dict()
        d["capabilities"].append("mutated")
        # mutating the dict's list must not affect the frozen card.
        assert card.capabilities == ["x"]

    def test_validate_missing_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            AgentCard.validate({"description": "d"})

    def test_validate_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            AgentCard.validate({"name": "", "description": "d"})

    def test_validate_missing_description_raises(self) -> None:
        with pytest.raises(ValueError, match="'description' string"):
            AgentCard.validate({"name": "a"})

    def test_validate_capabilities_must_be_list(self) -> None:
        with pytest.raises(ValueError, match="'capabilities' must be a list"):
            AgentCard.validate({"name": "a", "description": "d", "capabilities": "nope"})

    def test_validate_capability_items_must_be_strings(self) -> None:
        with pytest.raises(ValueError, match="capability at index 1"):
            AgentCard.validate({"name": "a", "description": "d", "capabilities": ["ok", 5]})

    def test_validate_accepts_valid_card(self) -> None:
        # no exception means valid.
        AgentCard.validate({"name": "a", "description": "d", "capabilities": ["x"]})

    def test_json_schema_shape(self) -> None:
        schema = AgentCard.json_schema()
        assert schema["required"] == ["name", "description"]
        assert schema["properties"]["name"]["minLength"] == 1
        assert schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Handler lifecycle
# ---------------------------------------------------------------------------


class TestHandlerLifecycle:
    def test_orchestrator_card(self) -> None:
        handler = A2AHandler(server_url="http://host:1234")
        card = handler.orchestrator_card()
        assert card.name == "bernstein-orchestrator"
        assert card.endpoint == "http://host:1234/a2a"
        assert "a2a_message" in card.capabilities

    def test_create_task_registers(self) -> None:
        handler = A2AHandler()
        task = handler.create_task(sender="peer", message="do x")
        assert task.sender == "peer"
        assert task.status == A2ATaskStatus.SUBMITTED
        assert handler.get_task(task.id) is task

    def test_task_ids_unique(self) -> None:
        handler = A2AHandler()
        ids = {handler.create_task("p", "g").id for _ in range(20)}
        assert len(ids) == 20

    def test_get_unknown_task_none(self) -> None:
        assert A2AHandler().get_task("ghost") is None

    def test_link_creates_reverse_index(self) -> None:
        handler = A2AHandler()
        task = handler.create_task("p", "g")
        handler.link_bernstein_task(task.id, "bern-1")
        assert handler.get_task(task.id).bernstein_task_id == "bern-1"
        assert handler.get_by_bernstein_id("bern-1") is task

    def test_get_by_unknown_bernstein_id_none(self) -> None:
        assert A2AHandler().get_by_bernstein_id("nope") is None

    def test_link_unknown_task_raises(self) -> None:
        with pytest.raises(KeyError):
            A2AHandler().link_bernstein_task("ghost", "bern-1")

    def test_sync_status_maps(self) -> None:
        handler = A2AHandler()
        task = handler.create_task("p", "g")
        new_status = handler.sync_status(task.id, "done")
        assert new_status == A2ATaskStatus.COMPLETED
        assert handler.get_task(task.id).status == A2ATaskStatus.COMPLETED

    def test_sync_status_unknown_bernstein_status_defaults(self) -> None:
        handler = A2AHandler()
        task = handler.create_task("p", "g")
        assert handler.sync_status(task.id, "weird-status") == A2ATaskStatus.SUBMITTED

    def test_sync_unknown_task_raises(self) -> None:
        with pytest.raises(KeyError):
            A2AHandler().sync_status("ghost", "done")

    def test_add_artifact(self) -> None:
        handler = A2AHandler()
        task = handler.create_task("p", "g")
        art = handler.add_artifact(task.id, name="result.md", data="content")
        assert art.name == "result.md"
        assert handler.get_task(task.id).artifacts == [art]

    def test_add_artifact_unknown_task_raises(self) -> None:
        with pytest.raises(KeyError):
            A2AHandler().add_artifact("ghost", name="x", data="y")

    def test_list_tasks_all_and_by_sender(self) -> None:
        handler = A2AHandler()
        handler.create_task("alice", "g1")
        handler.create_task("bob", "g2")
        assert len(handler.list_tasks()) == 2
        alice_tasks = handler.list_tasks(sender="alice")
        assert len(alice_tasks) == 1
        assert alice_tasks[0].sender == "alice"


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestMessages:
    def test_receive_message_records_inbound(self) -> None:
        handler = A2AHandler()
        msg = handler.receive_message(sender="ext", recipient="bernstein", content="hi", task_id="t1")
        assert msg.direction == "inbound"
        assert msg.delivered is True
        assert handler.list_messages() == (msg,)

    def test_list_messages_filtered_by_task(self) -> None:
        handler = A2AHandler()
        handler.receive_message(sender="a", recipient="b", content="1", task_id="t1")
        handler.receive_message(sender="a", recipient="b", content="2", task_id="t2")
        for_t1 = handler.list_messages(task_id="t1")
        assert len(for_t1) == 1
        assert for_t1[0].task_id == "t1"

    @pytest.mark.asyncio
    async def test_send_message_happy_path(self) -> None:
        posted: dict[str, Any] = {}

        class _FakeResponse:
            def raise_for_status(self) -> None:
                return None

        class _FakeClient:
            async def post(self, url: str, *, json: dict[str, Any]) -> _FakeResponse:
                posted["url"] = url
                posted["json"] = json
                return _FakeResponse()

        handler = A2AHandler()
        msg = await handler.send_message(
            sender="bernstein",
            recipient="peer",
            content="hello",
            task_id="t1",
            external_endpoint="http://peer.example.com/",
            client=_FakeClient(),  # type: ignore[arg-type]
        )
        assert msg.direction == "outbound"
        assert msg.delivered is True
        # trailing slash trimmed, /a2a/message appended.
        assert posted["url"] == "http://peer.example.com/a2a/message"
        assert posted["json"]["content"] == "hello"
        assert handler.list_messages(task_id="t1") == (msg,)

    @pytest.mark.asyncio
    async def test_send_message_propagates_http_error(self) -> None:
        class _BoomResponse:
            def raise_for_status(self) -> None:
                raise RuntimeError("502 Bad Gateway")

        class _BoomClient:
            async def post(self, url: str, *, json: dict[str, Any]) -> _BoomResponse:
                return _BoomResponse()

        handler = A2AHandler()
        with pytest.raises(RuntimeError, match="502"):
            await handler.send_message(
                sender="a",
                recipient="b",
                content="c",
                task_id="t1",
                external_endpoint="http://peer/",
                client=_BoomClient(),  # type: ignore[arg-type]
            )
        # a failed send must not record an outbound message.
        assert handler.list_messages() == ()
