"""Tests for GET /dashboard/tasks/{task_id}/trace.

The endpoint reads JSONL trace records from ``.sdd/traces/`` and returns a
chronologically-ordered timeline. We test:

* 404 when the task does not exist.
* Empty events list when the task exists but no trace has been recorded.
* JSONL parsing - multiple traces, ordering, malformed lines, has_open_trace.
* Pagination via ``limit`` + ``cursor``.
* Mounting under both ``/`` and ``/api/v1/`` (legacy + versioned surface).
"""

from __future__ import annotations

import json
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
def app(jsonl_path: Path, tmp_path: Path) -> FastAPI:
    application = create_app(jsonl_path=jsonl_path)
    # Pin workdir so the route reads tmp_path/.sdd/traces/ rather than cwd.
    application.state.workdir = tmp_path
    return application


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _write_jsonl(traces_dir: Path, task_id: str, lines: list[dict[str, object]]) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{task_id}.jsonl"
    path.write_text("\n".join(json.dumps(rec) for rec in lines) + "\n")


@pytest.mark.anyio()
async def test_trace_not_found(client: AsyncClient) -> None:
    """Unknown task → 404."""
    resp = await client.get("/dashboard/tasks/missing/trace")
    assert resp.status_code == 404


@pytest.mark.anyio()
async def test_trace_empty_when_no_jsonl(client: AsyncClient) -> None:
    """Task exists but no JSONL → 200 + empty events."""
    create = await client.post(
        "/tasks",
        json={"title": "no-trace", "description": "no trace yet", "role": "backend"},
    )
    task_id = create.json()["id"]

    resp = await client.get(f"/dashboard/tasks/{task_id}/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["events"] == []
    assert body["total"] == 0
    assert body["first_ts"] is None
    assert body["last_ts"] is None
    assert body["has_open_trace"] is False


@pytest.mark.anyio()
async def test_trace_flattens_steps_in_chronological_order(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Multiple trace records flatten to one ordered event list."""
    create = await client.post(
        "/tasks",
        json={"title": "with-trace", "description": "has steps", "role": "backend"},
    )
    task_id = create.json()["id"]

    traces_dir = tmp_path / ".sdd" / "traces"
    _write_jsonl(
        traces_dir,
        task_id,
        [
            {
                "trace_id": "abc123",
                "session_id": "sess-1",
                "task_ids": [task_id],
                "agent_role": "backend",
                "model": "sonnet",
                "effort": "high",
                "spawn_ts": 100.0,
                "end_ts": 150.0,
                "outcome": "success",
                "steps": [
                    {"type": "spawn", "timestamp": 100.0, "detail": "spawned"},
                    {"type": "edit", "timestamp": 130.0, "files": ["a.py"]},
                    {"type": "complete", "timestamp": 150.0, "detail": "done"},
                ],
            },
            {
                "trace_id": "def456",
                "session_id": "sess-2",
                "task_ids": [task_id],
                "agent_role": "qa",
                "model": "haiku",
                "effort": "med",
                "spawn_ts": 200.0,
                "end_ts": None,
                "outcome": "unknown",
                "steps": [
                    {"type": "orient", "timestamp": 210.0, "files": ["b.py"]},
                ],
            },
        ],
    )

    resp = await client.get(f"/dashboard/tasks/{task_id}/trace")
    assert resp.status_code == 200
    body = resp.json()
    # 2 trace_meta events + 4 step events
    assert body["total"] == 6
    kinds = [ev["kind"] for ev in body["events"]]
    # First the trace_meta@100, then steps@100,130,150, then trace_meta@200, step@210
    assert kinds[0] == "trace_meta"
    assert kinds[-1] == "orient"
    # Chronologically sorted timestamps
    timestamps = [ev["ts"] for ev in body["events"]]
    assert timestamps == sorted(timestamps)
    assert body["first_ts"] == 100.0
    assert body["last_ts"] == 210.0
    # second trace has end_ts=None → live
    assert body["has_open_trace"] is True
    # complete event tagged success
    complete_ev = next(ev for ev in body["events"] if ev["kind"] == "complete")
    assert complete_ev["outcome"] == "success"


@pytest.mark.anyio()
async def test_trace_malformed_lines_skipped(client: AsyncClient, tmp_path: Path) -> None:
    """Garbage/invalid lines do not break the response."""
    create = await client.post(
        "/tasks",
        json={"title": "junk-trace", "description": "with junk", "role": "backend"},
    )
    task_id = create.json()["id"]

    traces_dir = tmp_path / ".sdd" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{task_id}.jsonl").write_text(
        "not-json-at-all\n"
        + json.dumps(
            {
                "trace_id": "ok",
                "session_id": "sess",
                "task_ids": [task_id],
                "agent_role": "backend",
                "model": "sonnet",
                "spawn_ts": 1.0,
                "end_ts": 2.0,
                "outcome": "success",
                "steps": [{"type": "complete", "timestamp": 2.0, "detail": "ok"}],
            }
        )
        + "\n"
        + "{partial json\n"
    )

    resp = await client.get(f"/dashboard/tasks/{task_id}/trace")
    assert resp.status_code == 200
    body = resp.json()
    # 1 trace_meta + 1 step
    assert body["total"] == 2
    assert body["has_open_trace"] is False


@pytest.mark.anyio()
async def test_trace_pagination(client: AsyncClient, tmp_path: Path) -> None:
    """limit + cursor paginates and reports next cursor."""
    create = await client.post(
        "/tasks",
        json={"title": "big", "description": "many steps", "role": "backend"},
    )
    task_id = create.json()["id"]

    traces_dir = tmp_path / ".sdd" / "traces"
    steps = [{"type": "edit", "timestamp": float(i), "files": [f"f{i}.py"]} for i in range(10)]
    _write_jsonl(
        traces_dir,
        task_id,
        [
            {
                "trace_id": "t",
                "session_id": "s",
                "task_ids": [task_id],
                "agent_role": "backend",
                "model": "sonnet",
                "spawn_ts": 0.0,
                "end_ts": 10.0,
                "outcome": "success",
                "steps": steps,
            }
        ],
    )

    first = await client.get(f"/dashboard/tasks/{task_id}/trace?limit=4")
    body = first.json()
    assert body["total"] == 11  # 10 steps + 1 trace_meta
    assert len(body["events"]) == 4
    assert body["cursor"] == 4

    second = await client.get(f"/dashboard/tasks/{task_id}/trace?limit=4&cursor=4")
    body2 = second.json()
    assert len(body2["events"]) == 4
    assert body2["cursor"] == 8

    third = await client.get(f"/dashboard/tasks/{task_id}/trace?limit=4&cursor=8")
    body3 = third.json()
    assert len(body3["events"]) == 3
    assert body3["cursor"] is None


@pytest.mark.anyio()
async def test_trace_mounted_under_api_v1(client: AsyncClient, tmp_path: Path) -> None:
    """The same route must be reachable under the /api/v1 prefix."""
    create = await client.post(
        "/tasks",
        json={"title": "mounted", "description": "v1 mount", "role": "backend"},
    )
    task_id = create.json()["id"]

    traces_dir = tmp_path / ".sdd" / "traces"
    _write_jsonl(
        traces_dir,
        task_id,
        [
            {
                "trace_id": "x",
                "session_id": "sx",
                "task_ids": [task_id],
                "agent_role": "qa",
                "model": "haiku",
                "spawn_ts": 1.0,
                "end_ts": 2.0,
                "outcome": "success",
                "steps": [{"type": "complete", "timestamp": 2.0, "detail": "ok"}],
            }
        ],
    )

    resp = await client.get(f"/api/v1/dashboard/tasks/{task_id}/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["total"] == 2
