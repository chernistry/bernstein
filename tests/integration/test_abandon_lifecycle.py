"""Integration tests for the abandon primitive end-to-end (#1350).

These tests exercise the full vertical:
    TaskStore.abandon() → ledger write → CLI surface → aggregation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.abandonments_cmd import abandonments_group
from bernstein.core.tasks.abandon import AbandonmentLedger
from bernstein.core.tasks.models import Task, TaskStatus
from bernstein.core.tasks.task_store_core import TaskStore


def _make_store(workdir: Path) -> TaskStore:
    runtime = workdir / ".sdd" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return TaskStore(runtime / "tasks.jsonl", archive_path=workdir / ".sdd" / "archive" / "tasks.jsonl")


async def _seed(store: TaskStore, **overrides: Any) -> Task:
    base: dict[str, Any] = {
        "id": overrides.pop("id", "T-1"),
        "title": "t",
        "description": "d",
        "role": "backend",
        "status": TaskStatus.IN_PROGRESS,
    }
    base.update(overrides)
    task = Task(**base)
    store._tasks[task.id] = task  # type: ignore[attr-defined]
    store._index_add(task)  # type: ignore[attr-defined]
    return task


@pytest.mark.asyncio
class TestAbandonEndToEnd:
    async def test_abandon_appears_in_cli_list(self, tmp_path: Path) -> None:
        import json as _json

        store = _make_store(tmp_path)
        await _seed(store, id="T-1", role="backend")
        await store.abandon("T-1", "out_of_scope", "spec mismatch", adapter="claude")

        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["list", "--workdir", str(tmp_path), "--limit", "5", "--json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert len(data) == 1
        assert data[0]["task_id"] == "T-1"
        assert data[0]["reason"] == "out_of_scope"

    async def test_abandon_appears_in_cli_stats(self, tmp_path: Path) -> None:
        import json as _json

        store = _make_store(tmp_path)
        await _seed(store, id="T-1", role="backend")
        await _seed(store, id="T-2", role="qa")
        await store.abandon("T-1", "out_of_scope", adapter="claude")
        await store.abandon("T-2", "budget_exceeded", adapter="codex")

        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["stats", "--workdir", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["total"] == 2
        assert data["by_reason"]["out_of_scope"] == 1
        assert data["by_reason"]["budget_exceeded"] == 1
        assert data["by_role"]["backend"] == 1
        assert data["by_role"]["qa"] == 1

    async def test_downstream_consumer_cascades_to_blocked_by_abandon(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await _seed(store, id="T-upstream", role="backend")
        await _seed(store, id="T-down", role="qa", status=TaskStatus.OPEN, depends_on=["T-upstream"])
        await store.abandon("T-upstream", "insufficient_context")

        assert store._tasks["T-down"].status is TaskStatus.BLOCKED_BY_ABANDON  # type: ignore[attr-defined]

    async def test_metrics_aggregate_by_role_and_adapter(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Three tasks, two adapters, two roles
        await _seed(store, id="T-1", role="backend")
        await _seed(store, id="T-2", role="backend")
        await _seed(store, id="T-3", role="qa")
        await store.abandon("T-1", "out_of_scope", adapter="claude")
        await store.abandon("T-2", "budget_exceeded", adapter="claude")
        await store.abandon("T-3", "other", adapter="codex")

        ledger = AbandonmentLedger(tmp_path / ".sdd")
        rates_role = ledger.abandon_rate_by_role({"backend": 8, "qa": 1})
        assert rates_role["backend"] == pytest.approx(2 / 10)
        assert rates_role["qa"] == pytest.approx(1 / 2)

        rates_adapter = ledger.abandon_rate_by_adapter({"claude": 18, "codex": 4})
        assert rates_adapter["claude"] == pytest.approx(2 / 20)
        assert rates_adapter["codex"] == pytest.approx(1 / 5)

    async def test_ledger_survives_store_recreation(self, tmp_path: Path) -> None:
        """A new TaskStore reading the same workdir sees prior ledger rows."""
        store = _make_store(tmp_path)
        await _seed(store, id="T-1", role="backend")
        await store.abandon("T-1", "other", adapter="claude")

        ledger = AbandonmentLedger(tmp_path / ".sdd")
        assert len(ledger.read_all()) == 1

        # Second store on same workdir
        store2 = _make_store(tmp_path)
        await _seed(store2, id="T-2", role="qa")
        await store2.abandon("T-2", "budget_exceeded", adapter="codex")

        ledger2 = AbandonmentLedger(tmp_path / ".sdd")
        rows = ledger2.read_all()
        assert len(rows) == 2
        assert {r.task_id for r in rows} == {"T-1", "T-2"}

    async def test_abandon_persists_to_tasks_jsonl(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await _seed(store, id="T-1")
        await store.abandon("T-1", "operator_override", "by request")
        await store.flush_buffer()
        body = (tmp_path / ".sdd" / "runtime" / "tasks.jsonl").read_text(encoding="utf-8")
        assert '"status": "abandoned"' in body or '"abandoned"' in body

    async def test_cli_list_after_no_abandons(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["list", "--workdir", str(tmp_path)], terminal_width=200)
        assert result.exit_code == 0
        assert "No abandonments recorded" in result.output

    async def test_repeated_abandon_attempts_on_same_task_rejected(self, tmp_path: Path) -> None:
        """Second abandon on an already-abandoned task is rejected by the FSM."""
        from bernstein.core.tasks.lifecycle import IllegalTransitionError

        store = _make_store(tmp_path)
        await _seed(store, id="T-1")
        await store.abandon("T-1", "other")
        with pytest.raises(IllegalTransitionError):
            await store.abandon("T-1", "other")

    async def test_abandon_records_role_from_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        await _seed(store, id="T-1", role="frontend")
        await store.abandon("T-1", "capability_mismatch", adapter="claude")
        ledger = AbandonmentLedger(tmp_path / ".sdd")
        rows = ledger.read_all()
        assert rows[0].role == "frontend"

    async def test_cli_json_output(self, tmp_path: Path) -> None:
        import json as _json

        store = _make_store(tmp_path)
        await _seed(store, id="T-1", role="backend")
        await store.abandon("T-1", "out_of_scope", "spec issue", adapter="claude")

        runner = CliRunner()
        result = runner.invoke(abandonments_group, ["stats", "--workdir", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["total"] == 1
