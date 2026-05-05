"""Unit tests for fingerprint memoization.

Critical regression target: changing a memoized function's body MUST
change the fingerprint, so callers cannot serve stale outputs after a
bug fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.persistence.fingerprint import (
    MemoStore,
    default_store,
    fingerprint,
    memoize_persistent,
)


def _fn_v1(x: int, y: int) -> int:
    return x + y


def _fn_v2(x: int, y: int) -> int:
    # same signature, different body — fingerprint MUST diverge
    return x + y + 1


class TestFingerprintCore:
    def test_same_fn_same_args_same_key(self) -> None:
        assert fingerprint(_fn_v1, 1, 2) == fingerprint(_fn_v1, 1, 2)

    def test_same_fn_different_args_different_key(self) -> None:
        assert fingerprint(_fn_v1, 1, 2) != fingerprint(_fn_v1, 1, 3)

    def test_changed_function_body_changes_key(self) -> None:
        """Regression: this is the whole point of the work."""
        assert fingerprint(_fn_v1, 1, 2) != fingerprint(_fn_v2, 1, 2)

    def test_kwargs_order_does_not_matter(self) -> None:
        def f(*, a: int, b: int) -> int:
            return a + b

        assert fingerprint(f, a=1, b=2) == fingerprint(f, b=2, a=1)

    def test_digest_is_32_bytes(self) -> None:
        assert len(fingerprint(_fn_v1, 1, 2)) == 32

    def test_unhashable_args_fall_back_gracefully(self) -> None:
        digest = fingerprint(_fn_v1, {"complex": [1, 2, 3]})
        assert isinstance(digest, bytes)
        assert len(digest) == 32

    def test_two_distinct_fns_with_same_body_differ_by_qualname(self) -> None:
        def alpha(x: int) -> int:
            return x

        def beta(x: int) -> int:
            return x

        assert fingerprint(alpha, 1) != fingerprint(beta, 1)


class TestMemoStore:
    def test_get_miss_then_put_then_hit(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        digest = b"\x00" * 32
        assert store.get(digest) is None
        store.put(digest, {"answer": 42})
        assert store.get(digest) == {"answer": 42}

    def test_eviction_caps_total_size(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=0)
        store._max_bytes = 1024  # 1 KiB cap for test speed
        for i in range(50):
            store.put(bytes([i]) * 32, b"x" * 200)
        assert store.total_bytes() <= store._max_bytes

    def test_stats_track_hits_and_misses(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        digest = b"\x01" * 32
        assert store.get(digest) is None
        store.put(digest, "v")
        assert store.get(digest) == "v"
        stats = store.stats()
        assert stats.hits == 1
        assert stats.misses == 1


class TestMemoizePersistent:
    def test_decorator_caches_result(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        calls = {"n": 0}

        @memoize_persistent(store, site="test")
        def expensive(x: int) -> int:
            calls["n"] += 1
            return x * 2

        assert expensive(7) == 14
        assert expensive(7) == 14
        assert calls["n"] == 1

    def test_decorator_recomputes_when_inputs_change(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        calls = {"n": 0}

        @memoize_persistent(store, site="test")
        def expensive(x: int) -> int:
            calls["n"] += 1
            return x * 2

        expensive(1)
        expensive(2)
        expensive(3)
        assert calls["n"] == 3

    def test_default_store_uses_sdd_runtime_memo(self, tmp_path: Path) -> None:
        store = default_store(tmp_path)
        assert store.root == tmp_path / ".sdd" / "runtime" / "memo"


class TestPerfStress:
    """1000-entry stress test to confirm size cap holds under load."""

    @pytest.mark.parametrize("n_entries", [1000])
    def test_eviction_holds_under_1000_entries(self, tmp_path: Path, n_entries: int) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        store._max_bytes = 64 * 1024  # 64 KiB cap
        payload = b"y" * 256
        for i in range(n_entries):
            digest = i.to_bytes(4, "big") + b"\x00" * 28
            store.put(digest, payload)
        assert store.total_bytes() <= store._max_bytes
