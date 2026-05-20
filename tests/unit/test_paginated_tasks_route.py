"""WEB-011: Tests for paginated task list with sorting/filtering."""

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


async def _create_tasks(client: AsyncClient, count: int) -> list[str]:
    """Create N tasks and return their IDs."""
    ids: list[str] = []
    for i in range(count):
        resp = await client.post(
            "/tasks",
            json={
                "title": f"Task {i}",
                "description": f"Description {i}",
                "role": "backend" if i % 2 == 0 else "qa",
                "priority": (i % 3) + 1,
            },
        )
        assert resp.status_code == 201
        ids.append(resp.json()["id"])
    return ids


class TestPaginatedTaskSearch:
    """Test GET /tasks/search endpoint."""

    @pytest.mark.anyio()
    async def test_empty_search(self, client: AsyncClient) -> None:
        """Search with no tasks returns empty page."""
        resp = await client.get("/tasks/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["tasks"] == []
        assert data["page"] == 1
        assert data["per_page"] == 20

    @pytest.mark.anyio()
    async def test_pagination(self, client: AsyncClient) -> None:
        """Pagination splits results correctly."""
        await _create_tasks(client, 5)

        resp = await client.get("/tasks/search?page=1&per_page=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["tasks"]) == 2
        assert data["page"] == 1
        assert data["total_pages"] == 3

    @pytest.mark.anyio()
    async def test_page_2(self, client: AsyncClient) -> None:
        """Second page should return next set of results."""
        await _create_tasks(client, 5)

        resp = await client.get("/tasks/search?page=2&per_page=2")
        data = resp.json()
        assert len(data["tasks"]) == 2
        assert data["page"] == 2

    @pytest.mark.anyio()
    async def test_filter_by_role(self, client: AsyncClient) -> None:
        """Filter by role should return only matching tasks."""
        await _create_tasks(client, 4)

        resp = await client.get("/tasks/search?role=backend")
        data = resp.json()
        for task in data["tasks"]:
            assert task["role"] == "backend"

    @pytest.mark.anyio()
    async def test_sort_by_priority_asc(self, client: AsyncClient) -> None:
        """Sort by priority ascending."""
        await _create_tasks(client, 4)

        resp = await client.get("/tasks/search?sort=priority&order=asc")
        data = resp.json()
        priorities = [t["priority"] for t in data["tasks"]]
        assert priorities == sorted(priorities)

    @pytest.mark.anyio()
    async def test_sort_by_created_at_desc(self, client: AsyncClient) -> None:
        """Default sort by created_at descending."""
        await _create_tasks(client, 3)

        resp = await client.get("/tasks/search?sort=created_at&order=desc")
        data = resp.json()
        timestamps = [t["created_at"] for t in data["tasks"]]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.anyio()
    async def test_per_page_clamped(self, client: AsyncClient) -> None:
        """per_page above 100 should be clamped."""
        resp = await client.get("/tasks/search?per_page=999")
        data = resp.json()
        assert data["per_page"] == 100

    @pytest.mark.anyio()
    async def test_invalid_sort_falls_back(self, client: AsyncClient) -> None:
        """Invalid sort field falls back to created_at."""
        resp = await client.get("/tasks/search?sort=nonexistent")
        data = resp.json()
        assert data["sort"] == "created_at"

    @pytest.mark.anyio()
    async def test_filters_in_response(self, client: AsyncClient) -> None:
        """Applied filters should appear in the response metadata."""
        resp = await client.get("/tasks/search?status=open&role=backend")
        data = resp.json()
        assert data["filters"]["status"] == "open"
        assert data["filters"]["role"] == "backend"


class TestLegacyListTasksHardCap:
    """Regression: GET /tasks without limit/offset is capped at 500 items."""

    @pytest.mark.anyio()
    async def test_legacy_path_caps_response_at_500(
        self,
        client: AsyncClient,
        app: FastAPI,
    ) -> None:
        """Even with > 500 tasks the legacy flat list must not exceed 500."""
        # Bypass the per-request HTTP overhead: insert directly through the
        # store so the test stays fast on slow CI runners.
        from bernstein.core.models import Task, TaskStatus

        store = app.state.store
        async with store._lock:
            for i in range(600):
                task = Task(
                    id=f"t-{i:04d}",
                    title=f"task {i}",
                    description="",
                    role="backend",
                    status=TaskStatus.OPEN,
                    batch_eligible=False,
                )
                store._tasks[task.id] = task
                store._index_add(task)

        resp = await client.get("/tasks")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 500
        # Deprecation headers must accompany the legacy shape.
        assert resp.headers.get("Deprecation") == "true"
        assert resp.headers.get("X-Total-Count") == "600"
        assert "successor-version" in resp.headers.get("Link", "")
        # Warning header is only emitted when truncation actually happens.
        assert "299" in resp.headers.get("Warning", "")

    @pytest.mark.anyio()
    async def test_paginated_path_also_capped_at_500(self, client: AsyncClient) -> None:
        """Explicit pagination clamps limit to 500."""
        resp = await client.get("/tasks?limit=10000&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 500
        assert isinstance(data["tasks"], list)
        assert len(data["tasks"]) <= 500


class TestListTasksFilterParity:
    """Single-pass filter must return the same tasks as the old chain."""

    @pytest.mark.anyio()
    async def test_combined_filters_return_intersection(
        self,
        client: AsyncClient,
        app: FastAPI,
    ) -> None:
        from bernstein.core.models import Task, TaskStatus

        store = app.state.store
        async with store._lock:
            for i in range(20):
                task = Task(
                    id=f"f-{i:02d}",
                    title=f"task {i}",
                    description="",
                    role="backend" if i % 2 == 0 else "qa",
                    status=TaskStatus.OPEN,
                    cell_id="cell-1" if i < 10 else "cell-2",
                    claimed_by_session="sess-A" if i % 3 == 0 else None,
                    batch_eligible=False,
                )
                store._tasks[task.id] = task
                store._index_add(task)

        result = store.list_tasks(
            status="open",
            cell_id="cell-1",
            claimed_by_session="sess-A",
        )
        for t in result:
            assert t.cell_id == "cell-1"
            assert t.claimed_by_session == "sess-A"
            assert t.status == TaskStatus.OPEN
        # Sanity: result must be non-empty given the seed.
        assert result, "expected at least one matching task"


class TestClaimBatchTenantAuthz:
    """Tenant authz is enforced inside store.claim_batch (TOCTOU-safe)."""

    @pytest.mark.anyio()
    async def test_claim_batch_rejects_cross_tenant_tasks(
        self,
        client: AsyncClient,
        app: FastAPI,
    ) -> None:
        from bernstein.core.models import Task, TaskStatus

        store = app.state.store
        # Seed one team-a task and one team-b task.
        async with store._lock:
            a = Task(
                id="own-1",
                title="own",
                description="",
                role="backend",
                status=TaskStatus.OPEN,
                tenant_id="team-a",
                batch_eligible=False,
            )
            b = Task(
                id="other-1",
                title="other",
                description="",
                role="backend",
                status=TaskStatus.OPEN,
                tenant_id="team-b",
                batch_eligible=False,
            )
            store._tasks[a.id] = a
            store._tasks[b.id] = b
            store._index_add(a)
            store._index_add(b)

        # Caller authenticates as team-a and tries to claim both.
        resp = await client.post(
            "/tasks/claim-batch",
            json={
                "task_ids": ["own-1", "other-1"],
                "agent_id": "agent-x",
            },
            headers={"x-tenant-id": "team-a"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["claimed"] == ["own-1"]
        assert data["failed"] == ["other-1"]

    @pytest.mark.anyio()
    async def test_claim_batch_rejects_tenant_rewritten_between_calls(
        self,
        client: AsyncClient,
        app: FastAPI,
    ) -> None:
        """Simulate the TOCTOU window: tenant_id flips just before claim.

        With the old logic the task would already have passed the per-id
        get_task() check, so the claim_batch call would happily claim it.
        With the fix the check runs under the same lock that performs the
        claim, so the task is rejected.
        """
        from bernstein.core.models import Task, TaskStatus

        store = app.state.store
        async with store._lock:
            task = Task(
                id="race-1",
                title="race",
                description="",
                role="backend",
                status=TaskStatus.OPEN,
                tenant_id="team-a",
                batch_eligible=False,
            )
            store._tasks[task.id] = task
            store._index_add(task)

        # Flip tenant while no lock is held (analogous to a concurrent
        # rewrite landing between the route's pre-check and the claim).
        store._tasks["race-1"].tenant_id = "team-b"

        resp = await client.post(
            "/tasks/claim-batch",
            json={
                "task_ids": ["race-1"],
                "agent_id": "agent-x",
            },
            headers={"x-tenant-id": "team-a"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["claimed"] == []
        assert data["failed"] == ["race-1"]
        # Task must not have been mutated to claimed by the rejected call.
        assert store._tasks["race-1"].status == TaskStatus.OPEN
