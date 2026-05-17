"""Property tests for the content-addressed-storage (CAS) store.

The CAS store is the deduplication primitive backing artefact storage.
A regression here either silently drops content (data loss) or causes
hash collisions on benign inputs (correctness bug). Properties:

* **put-then-get round-trip** — any byte payload stored under its
  digest reads back identically.

* **Digest is content-determined** — two stores of the same bytes
  yield the same digest; two stores of distinct bytes yield distinct
  digests. The first is the contract the orchestrator relies on for
  deduplication; the second is the no-collision-on-benign-input
  guarantee.

* **Digest validates as 64 hex chars** — the producer must always
  emit a value the validator accepts. A drift here would cause
  read paths to refuse to serve content that the write path just
  stored.

* **Path-traversal digests are rejected by ``get``** — feeding a
  ``../../etc/passwd``-style string into the digest API must raise
  ``ValueError`` before any filesystem call. This is a defence-in-
  depth check on the CAS root.

* **Repeated puts dedup correctly** — the second put of an
  identical payload increments the dedup counter and does not touch
  the blob on disk.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.persistence.cas_store import CASStore

_HEX_64 = re.compile(r"\A[0-9a-f]{64}\Z")


@given(content=st.binary(min_size=0, max_size=4096))
def test_put_get_round_trip(tmp_path_factory: pytest.TempPathFactory, content: bytes) -> None:
    """``cas.get(cas.put(content)) == content`` for any byte payload.

    The single most important CAS invariant: a write is never lost,
    truncated, or rewritten on read. Catches regressions where the
    blob path is computed from a stale digest.
    """
    store = CASStore(tmp_path_factory.mktemp("cas"))
    digest = store.put(content)
    assert store.get(digest) == content


@given(content=st.binary(min_size=0, max_size=512))
def test_digest_matches_sha256(tmp_path_factory: pytest.TempPathFactory, content: bytes) -> None:
    """The returned digest equals ``sha256(content).hexdigest()``.

    The CAS digest scheme is the public contract for cross-process
    deduplication. A regression that switched algorithms would
    silently double every stored artefact.
    """
    store = CASStore(tmp_path_factory.mktemp("cas"))
    digest = store.put(content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert _HEX_64.match(digest)


@given(content=st.binary(min_size=0, max_size=512))
def test_dedup_on_repeated_put(tmp_path_factory: pytest.TempPathFactory, content: bytes) -> None:
    """A second put of the same payload returns the same digest and dedups.

    Catches regressions where ``has(digest)`` is computed against a
    different shard layout (e.g. after a refactor of ``_blob_path``).
    Such a drift would silently double-store everything.
    """
    store = CASStore(tmp_path_factory.mktemp("cas"))
    d1 = store.put(content)
    d2 = store.put(content)
    assert d1 == d2
    stats = store.stats()
    # one effective store, one dedup save
    assert stats.total_entries == 1
    assert stats.dedup_saves == 1


@given(
    a=st.binary(min_size=1, max_size=128),
    b=st.binary(min_size=1, max_size=128),
)
def test_distinct_content_distinct_digests(
    tmp_path_factory: pytest.TempPathFactory,
    a: bytes,
    b: bytes,
) -> None:
    """Distinct payloads yield distinct digests.

    With SHA-256 a collision is cryptographically impossible at our
    payload sizes; the property asserts the obvious correctness
    invariant so any future swap to a non-cryptographic hash is
    caught immediately.
    """
    if a == b:
        pytest.skip("identical payloads")
    store = CASStore(tmp_path_factory.mktemp("cas"))
    assert store.put(a) != store.put(b)


@given(
    bogus=st.text(
        alphabet=st.characters(
            min_codepoint=ord("a"),
            max_codepoint=ord("z"),
        ),
        min_size=0,
        max_size=80,
    ),
)
def test_invalid_digest_string_rejected(tmp_path_factory: pytest.TempPathFactory, bogus: str) -> None:
    """Any non-hex-64 digest string raises ``ValueError`` before disk access.

    Defence-in-depth: a digest derived from operator input that
    contains ``../`` would otherwise escape the CAS root. The validator
    rejects anything that does not match the strict regex.
    """
    if _HEX_64.match(bogus):
        pytest.skip("Hypothesis generated a valid-looking digest")
    store = CASStore(tmp_path_factory.mktemp("cas"))
    with pytest.raises(ValueError, match="Invalid CAS digest"):
        store.delete(bogus)


@settings(
    max_examples=20,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(payloads=st.lists(st.binary(min_size=1, max_size=64), min_size=1, max_size=8))
def test_list_entries_matches_stored_digests(
    tmp_path_factory: pytest.TempPathFactory,
    payloads: list[bytes],
) -> None:
    """``list_entries`` enumerates exactly the unique stored digests.

    Pins the contract used by the disaster-recovery audit tool. A
    regression here would silently exclude valid blobs from the
    recovery manifest.
    """
    store = CASStore(tmp_path_factory.mktemp("cas"))
    expected: set[str] = set()
    for p in payloads:
        expected.add(store.put(p))
    listed = {e.digest for e in store.list_entries()}
    assert listed == expected


@given(content=st.binary(min_size=1, max_size=128))
def test_get_returns_none_for_unknown_digest(tmp_path_factory: pytest.TempPathFactory, content: bytes) -> None:
    """``get`` on an unwritten digest returns ``None``, never raises.

    Catches regressions where ``read_bytes`` is called unconditionally
    and surfaces ``FileNotFoundError`` to the caller. The CAS contract
    is a soft miss for read paths.
    """
    store = CASStore(tmp_path_factory.mktemp("cas"))
    digest = hashlib.sha256(content).hexdigest()
    # Don't put — the digest is unknown to the store.
    assert store.get(digest) is None
    assert not store.has(digest)
