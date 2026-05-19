"""Unit tests for :mod:`bernstein.core.memory.cross_task_kb`."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from bernstein.core.memory.cross_task_kb import (
    CrossTaskKB,
    Fact,
    PublishCounter,
    redact_value,
)
from bernstein.core.memory.sqlite_store import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> SQLiteMemoryStore:
    """Return a fresh SQLite store backed by ``tmp_path/memory.db``."""
    return SQLiteMemoryStore(tmp_path / "memory.db")


@pytest.fixture
def kb(store: SQLiteMemoryStore) -> CrossTaskKB:
    """Return a facade for the canonical (run, producer) pair."""
    return CrossTaskKB(store, run_id="run-1", producer_task_id="task-a")


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


class TestPublish:
    def test_publish_returns_fact_with_attribution(self, kb: CrossTaskKB) -> None:
        fact = kb.publish(tag="api-schema", key="users", value="payload", scope="run")
        assert isinstance(fact, Fact)
        assert fact.tag == "api-schema"
        assert fact.key == "users"
        assert fact.value == "payload"
        assert fact.scope == "run"
        assert fact.producer_task_id == "task-a"
        assert fact.ts_ns > 0
        assert fact.content_hash.startswith("sha256:")

    def test_publish_writes_into_existing_memory_table(
        self,
        kb: CrossTaskKB,
        store: SQLiteMemoryStore,
    ) -> None:
        kb.publish(tag="api-schema", key="users", value="payload", scope="run")
        entries = store.list(limit=10)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.type == "cross_task"
        assert entry.content == "payload"
        # Tag-LIKE filter keeps working for operators querying the store directly.
        assert "api-schema" in entry.tags
        assert "cross_task:run" in entry.tags
        assert "key:users" in entry.tags

    def test_publish_validates_tag(self, kb: CrossTaskKB) -> None:
        with pytest.raises(ValueError, match="tag"):
            kb.publish(tag="", key="users", value="x", scope="run")
        with pytest.raises(ValueError, match=","):
            kb.publish(tag="a,b", key="users", value="x", scope="run")

    def test_publish_validates_key(self, kb: CrossTaskKB) -> None:
        with pytest.raises(ValueError, match="key"):
            kb.publish(tag="t", key="", value="x", scope="run")

    def test_publish_validates_scope(self, kb: CrossTaskKB) -> None:
        with pytest.raises(ValueError, match="scope"):
            kb.publish(tag="t", key="k", value="x", scope="global")  # type: ignore[arg-type]

    def test_publish_last_write_wins_with_warning(
        self,
        kb: CrossTaskKB,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        kb.publish(tag="t", key="k", value="v1", scope="run")
        with caplog.at_level("WARNING", logger="bernstein.core.memory.cross_task_kb"):
            kb.publish(tag="t", key="k", value="v2", scope="run")
        assert any("last-write-wins" in r.message for r in caplog.records)

    def test_publish_fires_hook_callback(self, store: SQLiteMemoryStore) -> None:
        events: list[tuple[str, dict[str, object]]] = []

        def dispatch(event: str, payload: dict[str, object]) -> None:
            events.append((event, payload))

        kb = CrossTaskKB(
            store,
            run_id="run-1",
            producer_task_id="task-a",
            hook_dispatch=dispatch,
        )
        kb.publish(tag="t", key="k", value="v", scope="run")
        assert len(events) == 1
        name, payload = events[0]
        assert name == "kb.fact_published"
        assert payload["tag"] == "t"
        assert payload["key"] == "k"
        assert payload["scope"] == "run"
        assert payload["producer_task_id"] == "task-a"
        assert isinstance(payload["ts_ns"], int)
        assert isinstance(payload["content_hash"], str)

    def test_publish_increments_counter(self, kb: CrossTaskKB) -> None:
        before_pub, _ = kb.counter.snapshot()
        kb.publish(tag="t", key="k", value="v", scope="run")
        kb.publish(tag="t", key="k2", value="v", scope="run")
        after_pub, _ = kb.counter.snapshot()
        assert after_pub - before_pub == 2

    def test_publish_hook_failure_is_swallowed(self, store: SQLiteMemoryStore) -> None:
        def boom(_event: str, _payload: dict[str, object]) -> None:
            raise RuntimeError("plugin exploded")

        kb = CrossTaskKB(
            store,
            run_id="run-1",
            producer_task_id="task-a",
            hook_dispatch=boom,
        )
        # Must not raise: a misbehaving plugin cannot break the publish path.
        fact = kb.publish(tag="t", key="k", value="v", scope="run")
        assert fact.tag == "t"


# ---------------------------------------------------------------------------
# Subscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_subscribe_returns_published_fact(self, kb: CrossTaskKB) -> None:
        kb.publish(tag="api-schema", key="users", value="payload", scope="run")
        facts = list(kb.subscribe(tag="api-schema", scope="run"))
        assert len(facts) == 1
        assert facts[0].key == "users"
        assert facts[0].value == "payload"
        assert facts[0].producer_task_id == "task-a"

    def test_subscribe_returns_one_per_key_latest_wins(self, kb: CrossTaskKB) -> None:
        kb.publish(tag="t", key="k", value="v1", scope="run")
        kb.publish(tag="t", key="k", value="v2", scope="run")
        facts = list(kb.subscribe(tag="t", scope="run"))
        assert len(facts) == 1
        assert facts[0].value == "v2"

    def test_subscribe_returns_facts_from_multiple_keys(self, kb: CrossTaskKB) -> None:
        kb.publish(tag="t", key="a", value="va", scope="run")
        kb.publish(tag="t", key="b", value="vb", scope="run")
        kb.publish(tag="t", key="c", value="vc", scope="run")
        facts = sorted(kb.subscribe(tag="t", scope="run"), key=lambda f: f.key)
        assert [f.key for f in facts] == ["a", "b", "c"]
        assert [f.value for f in facts] == ["va", "vb", "vc"]

    def test_subscribe_unknown_tag_empty(self, kb: CrossTaskKB) -> None:
        kb.publish(tag="t1", key="k", value="v", scope="run")
        assert list(kb.subscribe(tag="t2", scope="run")) == []

    def test_subscribe_validates_inputs(self, kb: CrossTaskKB) -> None:
        with pytest.raises(ValueError):
            list(kb.subscribe(tag="", scope="run"))
        with pytest.raises(ValueError):
            list(kb.subscribe(tag="t", scope="bad"))  # type: ignore[arg-type]

    def test_subscribe_increments_counter_by_result_count(
        self,
        kb: CrossTaskKB,
    ) -> None:
        kb.publish(tag="t", key="a", value="va", scope="run")
        kb.publish(tag="t", key="b", value="vb", scope="run")
        _, sub_before = kb.counter.snapshot()
        list(kb.subscribe(tag="t", scope="run"))
        _, sub_after = kb.counter.snapshot()
        assert sub_after - sub_before == 2


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------


class TestScopeIsolation:
    def test_run_scope_isolates_across_runs(self, store: SQLiteMemoryStore) -> None:
        kb_run_1 = CrossTaskKB(store, run_id="run-1", producer_task_id="t1")
        kb_run_2 = CrossTaskKB(store, run_id="run-2", producer_task_id="t2")
        kb_run_1.publish(tag="t", key="k", value="v1", scope="run")
        kb_run_2.publish(tag="t", key="k", value="v2", scope="run")

        run_1_facts = list(kb_run_1.subscribe(tag="t", scope="run"))
        run_2_facts = list(kb_run_2.subscribe(tag="t", scope="run"))

        assert len(run_1_facts) == 1
        assert run_1_facts[0].value == "v1"
        assert len(run_2_facts) == 1
        assert run_2_facts[0].value == "v2"

    def test_project_scope_visible_across_runs(self, store: SQLiteMemoryStore) -> None:
        kb_a = CrossTaskKB(store, run_id="run-1", producer_task_id="t1")
        kb_b = CrossTaskKB(store, run_id="run-2", producer_task_id="t2")
        kb_a.publish(tag="t", key="k", value="vp", scope="project")

        facts = list(kb_b.subscribe(tag="t", scope="project"))
        assert len(facts) == 1
        assert facts[0].value == "vp"

    def test_run_scope_does_not_leak_into_project(self, store: SQLiteMemoryStore) -> None:
        kb_a = CrossTaskKB(store, run_id="run-1", producer_task_id="t1")
        kb_a.publish(tag="t", key="k", value="v-run", scope="run")
        # A consumer reading project scope must not see run-only facts.
        kb_b = CrossTaskKB(store, run_id="run-2", producer_task_id="t2")
        assert list(kb_b.subscribe(tag="t", scope="project")) == []

    def test_project_scope_does_not_leak_into_run(self, store: SQLiteMemoryStore) -> None:
        kb_a = CrossTaskKB(store, run_id="run-1", producer_task_id="t1")
        kb_a.publish(tag="t", key="k", value="v-proj", scope="project")
        kb_b = CrossTaskKB(store, run_id="run-2", producer_task_id="t2")
        assert list(kb_b.subscribe(tag="t", scope="run")) == []


# ---------------------------------------------------------------------------
# Concurrent writes
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_concurrent_publish_persists_all_writes(self, store: SQLiteMemoryStore) -> None:
        """Many threads publish in parallel; the store keeps every write.

        SQLite serialises writes at the file level; we assert no writes are
        lost. Each thread publishes under a distinct (tag, key) so the
        subscribe path can recover the full set without last-write-wins
        collapsing rows.
        """
        n_threads = 16
        errors: list[BaseException] = []

        def publish(idx: int) -> None:
            try:
                kb = CrossTaskKB(
                    store,
                    run_id="run-1",
                    producer_task_id=f"task-{idx}",
                )
                kb.publish(tag="t", key=f"k{idx}", value=f"v{idx}", scope="run")
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"concurrent publish raised: {errors!r}"
        reader = CrossTaskKB(store, run_id="run-1", producer_task_id="reader")
        facts = list(reader.subscribe(tag="t", scope="run"))
        assert len(facts) == n_threads
        assert {f.key for f in facts} == {f"k{i}" for i in range(n_threads)}

    def test_concurrent_publish_same_key_yields_one_latest(
        self,
        store: SQLiteMemoryStore,
    ) -> None:
        """N threads publish to the same (tag, key); subscribe collapses to one fact."""
        n_threads = 16
        errors: list[BaseException] = []

        def publish(idx: int) -> None:
            try:
                kb = CrossTaskKB(
                    store,
                    run_id="run-1",
                    producer_task_id=f"task-{idx}",
                )
                kb.publish(tag="t", key="shared", value=f"v{idx}", scope="run")
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"concurrent publish raised: {errors!r}"
        reader = CrossTaskKB(store, run_id="run-1", producer_task_id="reader")
        facts = list(reader.subscribe(tag="t", scope="run"))
        assert len(facts) == 1
        assert facts[0].key == "shared"


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_redact_value_masks_email(self) -> None:
        assert redact_value("ping ada@example.org now") == "ping [REDACTED] now"

    def test_redact_value_passes_through_clean_text(self) -> None:
        assert redact_value("no pii here") == "no pii here"

    def test_redact_value_masks_credit_card(self) -> None:
        assert "[REDACTED]" in redact_value("card 4242 4242 4242 4242 ok")


# ---------------------------------------------------------------------------
# Counter sanity
# ---------------------------------------------------------------------------


class TestPublishCounter:
    def test_counter_is_thread_safe(self) -> None:
        counter = PublishCounter()
        n = 200

        def bump() -> None:
            for _ in range(n):
                counter.incr_publish()
                counter.incr_subscribe()

        threads = [threading.Thread(target=bump) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        pub, sub = counter.snapshot()
        assert pub == 8 * n
        assert sub == 8 * n
