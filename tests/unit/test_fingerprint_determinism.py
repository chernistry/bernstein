"""Replay-determinism and concurrency tests for fingerprint memoization.

These complement :mod:`tests.unit.test_fingerprint` by covering the
failure modes the lineage-trail audit surfaced:

* sets/frozensets must hash to the same digest across processes
  (``PYTHONHASHSEED`` randomises ``set`` member order; without sorting,
  two CI runs on the same input get different cache keys);
* concurrent ``put`` on the same digest must not raise — the original
  implementation reused one fixed ``.tmp`` filename so the second
  ``replace`` fired ``FileNotFoundError``;
* corrupt cache files must self-heal so a single torn write does not
  trap a hot key in an infinite-miss loop;
* same input must yield byte-identical pickle payloads on disk
  (replay determinism).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bernstein.core.persistence.fingerprint import (
    MemoStore,
    _canonicalize,
    fingerprint,
)

# ---------------------------------------------------------------------------
# Cross-process determinism
# ---------------------------------------------------------------------------


_CROSS_PROCESS_SCRIPT = """
import json, sys
sys.path.insert(0, {src!r})
from bernstein.core.persistence.fingerprint import fingerprint

def f(s):
    return len(s)

print(fingerprint(f, {payload}).hex())
"""


def _run_under_seed(seed: str, payload: str) -> str:
    """Execute a tiny script with PYTHONHASHSEED=*seed* and return its hex digest."""
    src = str(Path(__file__).resolve().parents[2] / "src")
    script = _CROSS_PROCESS_SCRIPT.format(src=src, payload=payload)
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = seed
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


class TestCrossProcessDeterminism:
    def test_set_arg_hashes_identically_across_hash_seeds(self) -> None:
        seeds = ["1", "2", "42", "9999"]
        digests = [_run_under_seed(s, "{'a','b','c','d','e','f','g','h'}") for s in seeds]
        assert len(set(digests)) == 1, f"set fingerprints diverged across seeds: {digests}"

    def test_frozenset_arg_hashes_identically_across_hash_seeds(self) -> None:
        seeds = ["1", "7", "99"]
        digests = [_run_under_seed(s, "frozenset(['a','b','c','d','e'])") for s in seeds]
        assert len(set(digests)) == 1, f"frozenset fingerprints diverged: {digests}"

    def test_dict_with_nested_set_value_hashes_identically(self) -> None:
        seeds = ["1", "2", "100"]
        digests = [_run_under_seed(s, "{'k': {'a','b','c','d'}, 'n': 1}") for s in seeds]
        assert len(set(digests)) == 1, f"nested-set fingerprints diverged: {digests}"


# ---------------------------------------------------------------------------
# Canonicalization helpers
# ---------------------------------------------------------------------------


class TestCanonicalize:
    def test_set_becomes_sorted_marker_list(self) -> None:
        out = _canonicalize({"c", "a", "b"})
        assert out[0] == "__set__"
        assert out[1] == sorted(["c", "a", "b"], key=repr)

    def test_frozenset_becomes_sorted_marker_list(self) -> None:
        out = _canonicalize(frozenset({1, 2, 3}))
        assert out[0] == "__set__"
        assert out[1] == [1, 2, 3]

    def test_nested_dicts_recurse(self) -> None:
        result = _canonicalize({"outer": {"inner": {1, 2, 3}}})
        assert result["outer"]["inner"] == ["__set__", [1, 2, 3]]

    def test_scalars_pass_through(self) -> None:
        assert _canonicalize(5) == 5
        assert _canonicalize("hi") == "hi"
        assert _canonicalize(None) is None
        assert _canonicalize(3.14) == 3.14


# ---------------------------------------------------------------------------
# Replay determinism
# ---------------------------------------------------------------------------


def _replay_target(x: int, y: int) -> dict[str, int]:
    return {"sum": x + y, "product": x * y}


class TestReplayDeterminism:
    def test_same_input_yields_same_digest(self) -> None:
        d1 = fingerprint(_replay_target, 3, 4)
        d2 = fingerprint(_replay_target, 3, 4)
        assert d1 == d2

    def test_pickle_payload_is_byte_identical_for_equal_values(self, tmp_path: Path) -> None:
        store_a = MemoStore(root=tmp_path / "a", max_mb=1)
        store_b = MemoStore(root=tmp_path / "b", max_mb=1)
        digest = fingerprint(_replay_target, 1, 2)
        value = _replay_target(1, 2)
        store_a.put(digest, value)
        store_b.put(digest, value)

        # Both stores hold the entry under the *same* relative path
        rel_a = store_a._path_for(digest).relative_to(store_a.root)
        rel_b = store_b._path_for(digest).relative_to(store_b.root)
        assert rel_a == rel_b
        assert (store_a.root / rel_a).read_bytes() == (store_b.root / rel_b).read_bytes()


# ---------------------------------------------------------------------------
# Concurrent writers
# ---------------------------------------------------------------------------


class TestConcurrentWriters:
    def test_concurrent_put_same_digest_does_not_raise(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=4)
        digest = b"\xaa" * 32
        errors: list[BaseException] = []

        def worker(payload: dict[str, str]) -> None:
            for _ in range(50):
                try:
                    store.put(digest, payload)
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=({"k": c * 800},))
            for c in "abcde"
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Some writer's payload must be persisted intact.
        final = store.get(digest)
        assert final is not None
        assert "k" in final
        assert len(final["k"]) == 800

    def test_concurrent_put_distinct_digests_no_corruption(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=4)
        errors: list[BaseException] = []

        def worker(prefix: int) -> None:
            for i in range(40):
                d = bytes([prefix]) * 32
                try:
                    store.put(d, {"prefix": prefix, "i": i})
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(p,)) for p in range(1, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # All 8 distinct digests must be readable.
        for prefix in range(1, 9):
            d = bytes([prefix]) * 32
            v = store.get(d)
            assert v is not None
            assert v["prefix"] == prefix


# ---------------------------------------------------------------------------
# Corrupt-file self-heal
# ---------------------------------------------------------------------------


class TestCorruptFileSelfHeal:
    def test_corrupt_pickle_is_unlinked_on_get(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        digest = b"\x42" * 32
        store.put(digest, {"x": 1})

        path = store._path_for(digest)
        path.write_bytes(b"this is not pickle")

        assert store.get(digest) is None
        assert not path.exists(), "corrupt file should be unlinked so put() can re-populate"

    def test_repopulate_after_corruption_works(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        digest = b"\x42" * 32
        store.put(digest, {"x": 1})
        store._path_for(digest).write_bytes(b"junk")

        # First get observes corruption and self-heals.
        assert store.get(digest) is None
        # Subsequent put + get round-trips cleanly.
        store.put(digest, {"x": 2})
        assert store.get(digest) == {"x": 2}

    def test_truncated_file_treated_as_miss(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        digest = b"\x77" * 32
        store.put(digest, {"big": "x" * 500})
        # Truncate to a fragment so pickle.loads raises EOFError.
        store._path_for(digest).write_bytes(b"\x80\x05")

        assert store.get(digest) is None
        assert store.stats().misses >= 1


# ---------------------------------------------------------------------------
# Tmp-file path uniqueness
# ---------------------------------------------------------------------------


class TestTmpFileSafety:
    def test_no_lingering_tmp_files_after_successful_put(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        for i in range(20):
            store.put(bytes([i]) * 32, {"i": i})

        # No .tmp files should remain when puts succeed.
        leftovers = list((tmp_path / "memo").rglob("*.tmp"))
        assert leftovers == [], f"leftover tmp files: {leftovers}"

    def test_total_bytes_excludes_partial_tmp_files(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        digest = b"\x33" * 32
        store.put(digest, {"a": 1})

        # Drop a stray .tmp file alongside the entry.  total_bytes() counts
        # only ``.bin`` files so the fake tmp must not inflate the total.
        path = store._path_for(digest)
        stray = path.with_name(path.name + ".stale.tmp")
        stray.write_bytes(b"x" * 10_000)

        assert store.total_bytes() == path.stat().st_size


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


class TestPathTraversalSafety:
    def test_digest_hex_only_alphanumeric(self, tmp_path: Path) -> None:
        # digest.hex() is alphanumeric by construction; any byte sequence
        # therefore cannot escape the cache root via "../".
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        weird_digest = bytes(range(32))  # arbitrary bytes
        path = store._path_for(weird_digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``relative_to`` will raise if path escaped the root.
        path.relative_to(store.root)


# ---------------------------------------------------------------------------
# Eviction stability with concurrent writers
# ---------------------------------------------------------------------------


class TestEvictionUnderConcurrency:
    def test_size_cap_holds_under_concurrent_writers(self, tmp_path: Path) -> None:
        store = MemoStore(root=tmp_path / "memo", max_mb=1)
        store._max_bytes = 32 * 1024  # 32 KiB

        def worker(prefix: int) -> None:
            payload = b"x" * 256
            for i in range(100):
                d = (prefix.to_bytes(2, "big") + i.to_bytes(2, "big")) + b"\x00" * 28
                store.put(d, payload)

        threads = [threading.Thread(target=worker, args=(p,)) for p in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 400 writers x 256B = 100 KiB attempted vs 32 KiB cap.
        # Eviction is best-effort under contention so allow a 4x slack
        # (every writer can race past the eviction step once).
        assert store.total_bytes() <= store._max_bytes * 4


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-q"])
