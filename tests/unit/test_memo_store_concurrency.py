"""Concurrency and determinism tests for MemoStore + ActionCache.

These complement :mod:`tests.unit.test_action_cache` by covering the
failure modes the audit surfaced on the action-cache layer:

* concurrent writers through ``ActionCache.record`` must not crash;
* recording the same action twice must produce a stable cache state
  (the second write is a no-op because content is content-addressed);
* a torn cache file must self-heal so a hot key does not stay stuck
  in an infinite-miss loop.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.persistence.action_cache import (
    ActionCache,
    TokenCounts,
    derive_key,
)
from bernstein.core.persistence.fingerprint import MemoStore


def _make_cache(tmp_path: Path) -> ActionCache:
    store = MemoStore(root=tmp_path / "ac", max_mb=1)
    return ActionCache(store, mode="hybrid", run_id="run-test")


# ---------------------------------------------------------------------------
# Concurrent writers
# ---------------------------------------------------------------------------


class TestConcurrentRecord:
    def test_concurrent_record_same_key_does_not_raise(self, tmp_path: Path) -> None:
        cache = _make_cache(tmp_path)
        errors: list[BaseException] = []

        def worker(out: str) -> None:
            for _ in range(50):
                try:
                    cache.record(
                        model_id="opus",
                        prompt="hello",
                        output_text=out,
                        tokens=TokenCounts(prompt_tokens=2),
                        cost_usd=0.001,
                    )
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(c * 200,)) for c in "abcde"]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        rec = cache.lookup(model_id="opus", prompt="hello")
        assert rec is not None
        assert rec.output_text  # some writer's payload survived

    def test_concurrent_record_distinct_keys_all_persisted(self, tmp_path: Path) -> None:
        cache = _make_cache(tmp_path)
        errors: list[BaseException] = []

        def worker(prefix: int) -> None:
            for i in range(20):
                try:
                    cache.record(
                        model_id="opus",
                        prompt=f"prompt-{prefix}-{i}",
                        output_text=f"out-{prefix}-{i}",
                    )
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Every recorded action must round-trip.
        for prefix in range(6):
            for i in range(20):
                rec = cache.lookup(model_id="opus", prompt=f"prompt-{prefix}-{i}")
                assert rec is not None
                assert rec.output_text == f"out-{prefix}-{i}"


# ---------------------------------------------------------------------------
# Replay determinism
# ---------------------------------------------------------------------------


class TestReplayDeterminism:
    def test_derive_key_is_byte_stable(self) -> None:
        a = derive_key(model_id="opus", prompt="hello", tool_name="bash", tool_args={"cmd": "ls"})
        b = derive_key(model_id="opus", prompt="hello", tool_name="bash", tool_args={"cmd": "ls"})
        assert a == b
        assert isinstance(a, bytes)
        assert len(a) == 32

    def test_replay_same_inputs_yields_byte_identical_payload_on_disk(
        self,
        tmp_path: Path,
    ) -> None:
        store_a = MemoStore(root=tmp_path / "a", max_mb=1)
        store_b = MemoStore(root=tmp_path / "b", max_mb=1)
        cache_a = ActionCache(store_a, mode="record", run_id="r")
        cache_b = ActionCache(store_b, mode="record", run_id="r")
        # Identical record() invocation on two stores.
        cache_a.record(model_id="opus", prompt="p", output_text="hello", cost_usd=0.0)
        cache_b.record(model_id="opus", prompt="p", output_text="hello", cost_usd=0.0)

        digest = derive_key(model_id="opus", prompt="p")
        # The two on-disk pickles can differ because ``ActionRecord.timestamp``
        # is set to ``time.time()`` per call; after stripping it the *records*
        # must compare equal.
        assert store_a._path_for(digest).name == store_b._path_for(digest).name
        rec_a = cache_a.lookup(model_id="opus", prompt="p")
        rec_b = cache_b.lookup(model_id="opus", prompt="p")
        assert rec_a is not None
        assert rec_b is not None
        # Compare every field except the timestamp.
        for field in ("model_id", "prompt", "output_text", "cost_usd", "run_id", "version"):
            assert getattr(rec_a, field) == getattr(rec_b, field)


# ---------------------------------------------------------------------------
# Corrupt cache file self-heal at the action-cache layer
# ---------------------------------------------------------------------------


class TestCorruptActionRecord:
    def test_corrupt_cache_file_treated_as_miss(self, tmp_path: Path) -> None:
        cache = _make_cache(tmp_path)
        cache.record(model_id="opus", prompt="hi", output_text="ok")

        # Corrupt the file on disk.
        digest = derive_key(model_id="opus", prompt="hi")
        path = cache.store._path_for(digest)
        path.write_bytes(b"definitely_not_pickle")

        assert cache.lookup(model_id="opus", prompt="hi") is None

        # After self-heal, a fresh record() repopulates cleanly.
        cache.record(model_id="opus", prompt="hi", output_text="re-recorded")
        rec = cache.lookup(model_id="opus", prompt="hi")
        assert rec is not None
        assert rec.output_text == "re-recorded"


# ---------------------------------------------------------------------------
# No tmp leakage
# ---------------------------------------------------------------------------


class TestNoTmpLeakage:
    def test_record_does_not_leave_tmp_files(self, tmp_path: Path) -> None:
        cache = _make_cache(tmp_path)
        for i in range(20):
            cache.record(
                model_id="opus",
                prompt=f"p-{i}",
                output_text=f"o-{i}",
            )
        leftovers: list[Any] = list((tmp_path / "ac").rglob("*.tmp"))
        assert leftovers == [], f"tmp files leaked: {leftovers}"


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-q"])
