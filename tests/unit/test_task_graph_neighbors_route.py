"""Tests for GET /tasks/{task_id}/graph-neighbors (Deps tab data source)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from bernstein.core.server import create_app


@pytest.fixture()
def jsonl_path(tmp_path: Path) -> Path:
    return tmp_path / "tasks.jsonl"


@pytest.fixture()
def app(jsonl_path: Path) -> FastAPI:
    return create_app(jsonl_path=jsonl_path)


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_task(
    client: AsyncClient,
    title: str,
    depends_on: list[str] | None = None,
) -> str:
    payload: dict[str, object] = {
        "title": title,
        "description": title,
        "role": "backend",
    }
    if depends_on is not None:
        payload["depends_on"] = depends_on
    resp = await client.post("/tasks", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestTaskGraphNeighbors:
    """The Deps tab needs upstream + downstream for the selected task."""

    @pytest.mark.anyio()
    async def test_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/tasks/does-not-exist/graph-neighbors")
        assert resp.status_code == 404

    @pytest.mark.anyio()
    async def test_isolated_task_has_empty_neighbours(self, client: AsyncClient) -> None:
        task_id = await _create_task(client, "Solo")
        resp = await client.get(f"/tasks/{task_id}/graph-neighbors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["depth"] == 1
        assert data["upstream"] == []
        assert data["downstream"] == []

    @pytest.mark.anyio()
    async def test_upstream_reports_depends_on(self, client: AsyncClient) -> None:
        up_a = await _create_task(client, "Upstream A")
        up_b = await _create_task(client, "Upstream B")
        leaf = await _create_task(client, "Leaf", depends_on=[up_a, up_b])

        resp = await client.get(f"/tasks/{leaf}/graph-neighbors")
        assert resp.status_code == 200
        data = resp.json()

        up_ids = {item["id"] for item in data["upstream"]}
        assert up_ids == {up_a, up_b}
        # Every upstream entry exposes the fields the GUI needs to render.
        for item in data["upstream"]:
            assert set(item.keys()) >= {"id", "title", "status", "role"}
        assert data["downstream"] == []

    @pytest.mark.anyio()
    async def test_downstream_reports_dependents(self, client: AsyncClient) -> None:
        root = await _create_task(client, "Root")
        child_a = await _create_task(client, "Child A", depends_on=[root])
        child_b = await _create_task(client, "Child B", depends_on=[root])

        resp = await client.get(f"/tasks/{root}/graph-neighbors")
        assert resp.status_code == 200
        data = resp.json()

        assert data["upstream"] == []
        down_ids = {item["id"] for item in data["downstream"]}
        assert down_ids == {child_a, child_b}

    @pytest.mark.anyio()
    async def test_api_v1_alias(self, client: AsyncClient) -> None:
        """Route must be exposed under /api/v1/ as well as the legacy root."""
        task_id = await _create_task(client, "Versioned")
        resp = await client.get(f"/api/v1/tasks/{task_id}/graph-neighbors")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id
