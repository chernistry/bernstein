"""Tests for the standalone ``protocols/acp`` BeeAI bridge.

Covers the ACP run lifecycle and discovery surface:

* ``ACPRunStatus.from_bernstein`` status mapping (every documented key
  plus the unknown-status fallback).
* ``ACPRun.to_dict`` serialisation contract.
* ``ACPHandler`` discovery doc + agent metadata shape.
* run creation, lookup, task linking, cancellation, status sync, and
  listing - including the ``KeyError`` paths for unknown run IDs.

The handler is in-memory and deterministic, so every assertion pins an
observable fact.
"""

from __future__ import annotations

import pytest

from bernstein.core.protocols.acp import (
    ACPHandler,
    ACPRun,
    ACPRunStatus,
)


class TestACPRunStatusMapping:
    @pytest.mark.parametrize(
        ("bernstein_status", "expected"),
        [
            ("open", ACPRunStatus.CREATED),
            ("claimed", ACPRunStatus.RUNNING),
            ("in_progress", ACPRunStatus.RUNNING),
            ("blocked", ACPRunStatus.RUNNING),
            ("done", ACPRunStatus.COMPLETED),
            ("failed", ACPRunStatus.FAILED),
            ("cancelled", ACPRunStatus.CANCELLED),
        ],
    )
    def test_known_status_mapping(self, bernstein_status: str, expected: ACPRunStatus) -> None:
        assert ACPRunStatus.from_bernstein(bernstein_status) == expected

    def test_unknown_status_defaults_to_created(self) -> None:
        assert ACPRunStatus.from_bernstein("totally-unknown") == ACPRunStatus.CREATED

    def test_status_enum_values(self) -> None:
        assert ACPRunStatus.RUNNING.value == "running"
        assert ACPRunStatus.COMPLETED.value == "completed"


class TestACPRunSerialisation:
    def test_to_dict_contains_acp_fields(self) -> None:
        run = ACPRun(id="abc123", input_text="build it", role="qa")
        d = run.to_dict()
        assert d["run_id"] == "abc123"
        assert d["input"] == "build it"
        assert d["role"] == "qa"
        assert d["status"] == "created"
        assert d["bernstein_task_id"] is None

    def test_to_dict_reflects_linked_task(self) -> None:
        run = ACPRun(id="abc123", bernstein_task_id="task-9")
        assert run.to_dict()["bernstein_task_id"] == "task-9"

    def test_default_role_is_backend(self) -> None:
        assert ACPRun(id="x").role == "backend"


class TestACPHandlerDiscovery:
    def test_discovery_doc_protocol_and_version(self) -> None:
        handler = ACPHandler(server_url="http://host:9999")
        doc = handler.discovery_doc()
        assert doc["protocol"] == "acp"
        assert doc["version"] == "v0"
        assert doc["agents"][0]["name"] == "bernstein"
        assert doc["agents"][0]["endpoint"] == "http://host:9999/acp/v0"

    def test_agent_metadata_capabilities(self) -> None:
        handler = ACPHandler()
        meta = handler.agent_metadata()
        assert meta["name"] == "bernstein"
        assert meta["protocol_version"] == "v0"
        cap_names = {c["name"] for c in meta["capabilities"]}
        assert {"orchestrate", "cost_governance", "verify", "multi_agent"} <= cap_names

    def test_orchestrate_capability_requires_input(self) -> None:
        handler = ACPHandler()
        meta = handler.agent_metadata()
        orchestrate = next(c for c in meta["capabilities"] if c["name"] == "orchestrate")
        assert orchestrate["input_schema"]["required"] == ["input"]


class TestACPHandlerLifecycle:
    def test_create_run_registers_and_returns(self) -> None:
        handler = ACPHandler()
        run = handler.create_run("do work", role="security")
        assert run.input_text == "do work"
        assert run.role == "security"
        assert run.status == ACPRunStatus.CREATED
        # registered and retrievable.
        assert handler.get_run(run.id) is run

    def test_run_ids_are_unique(self) -> None:
        handler = ACPHandler()
        ids = {handler.create_run("g").id for _ in range(20)}
        assert len(ids) == 20

    def test_get_unknown_run_returns_none(self) -> None:
        assert ACPHandler().get_run("ghost") is None

    def test_link_bernstein_task(self) -> None:
        handler = ACPHandler()
        run = handler.create_run("g")
        handler.link_bernstein_task(run.id, "task-42")
        assert handler.get_run(run.id).bernstein_task_id == "task-42"

    def test_link_unknown_run_raises(self) -> None:
        handler = ACPHandler()
        with pytest.raises(KeyError):
            handler.link_bernstein_task("ghost", "task-42")

    def test_cancel_run_sets_cancelled_status(self) -> None:
        handler = ACPHandler()
        run = handler.create_run("g")
        prev_updated = run.updated_at
        cancelled = handler.cancel_run(run.id)
        assert cancelled.status == ACPRunStatus.CANCELLED
        assert cancelled.updated_at >= prev_updated

    def test_cancel_unknown_run_raises(self) -> None:
        with pytest.raises(KeyError):
            ACPHandler().cancel_run("ghost")

    def test_sync_status_maps_bernstein_status(self) -> None:
        handler = ACPHandler()
        run = handler.create_run("g")
        new_status = handler.sync_status(run.id, "done")
        assert new_status == ACPRunStatus.COMPLETED
        assert handler.get_run(run.id).status == ACPRunStatus.COMPLETED

    def test_sync_status_running_states(self) -> None:
        handler = ACPHandler()
        run = handler.create_run("g")
        assert handler.sync_status(run.id, "in_progress") == ACPRunStatus.RUNNING

    def test_sync_unknown_run_raises(self) -> None:
        with pytest.raises(KeyError):
            ACPHandler().sync_status("ghost", "done")

    def test_list_runs_returns_all(self) -> None:
        handler = ACPHandler()
        r1 = handler.create_run("a")
        r2 = handler.create_run("b")
        listed_ids = {r.id for r in handler.list_runs()}
        assert listed_ids == {r1.id, r2.id}

    def test_list_runs_empty_initially(self) -> None:
        assert ACPHandler().list_runs() == []
