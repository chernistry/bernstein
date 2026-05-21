"""Property-based tests for lineage v1 invariants (ADR-009 §12.2).

Each property covers one invariant and runs up to 500 examples under the
``smoke`` profile (the ``deep`` nightly profile lifts that further). The
shapes generated:

  - random well-formed LineageEntry instances
  - random linear chains over a single artefact
  - random fork shapes (parent + N siblings + optional merge)
  - random byte payloads for JCS determinism + JWS roundtrip
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import hypothesis.strategies as st
from hypothesis import HealthCheck, assume, given, settings

from bernstein.core.lineage.entry import (
    ARTEFACT_KINDS,
    LineageEntry,
    canonicalise,
    entry_hash,
)
from bernstein.core.lineage.identity import (
    AgentCard,
    generate_keypair,
    sign_detached,
    verify_detached,
)
from bernstein.core.lineage.tips import compute_tips, detect_forks

# ── Strategies ──────────────────────────────────────────────────────────────


_SHA = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64).map(lambda h: "sha256:" + h)
_PATH = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="/_.-",
    ),
    min_size=1,
    max_size=40,
).filter(lambda s: ".." not in s and not s.startswith("/"))
_AGENT_ID = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz-0123456789",
    min_size=1,
    max_size=20,
).map(lambda s: f"agent:{s}")
_KIND = st.sampled_from(sorted(ARTEFACT_KINDS))


def _entry_strategy() -> st.SearchStrategy[LineageEntry]:
    return st.builds(
        LineageEntry,
        v=st.just(1),
        artefact_path=_PATH,
        artefact_kind=_KIND,
        content_hash=_SHA,
        parent_hashes=st.lists(_SHA, max_size=3),
        agent_id=_AGENT_ID,
        agent_card_kid=st.text(min_size=1, max_size=20),
        tool_call_id=st.text(min_size=1, max_size=20),
        span_id=st.text(min_size=1, max_size=20),
        ts_ns=st.integers(min_value=0, max_value=10**18),
        operator_hmac=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    )


# Pre-generated key reused across property runs to avoid Ed25519 keygen cost.
_PRIV, _PUB = generate_keypair()
_CARD = AgentCard(agent_id="agent:t", kid="k1", public_key_pem=_PUB)


# ── Property 1: Chain integrity - every parent_hash resolves ───────────────


@given(st.integers(min_value=1, max_value=20))
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_chain_integrity_every_parent_resolves(length: int) -> None:
    """Build a linear chain of `length` entries; every parent_hash must point
    to an entry that exists in the log."""
    entries: list[LineageEntry] = []
    prev: list[str] = []
    for i in range(length):
        e = LineageEntry(
            v=1,
            artefact_path="x.py",
            artefact_kind="file",
            content_hash="sha256:" + hashlib.sha256(f"e{i}".encode()).hexdigest(),
            parent_hashes=prev,
            agent_id="agent:t",
            agent_card_kid="k1",
            tool_call_id=f"tc-{i}",
            span_id=f"span-{i}",
            ts_ns=i,
            operator_hmac="00" * 32,
        )
        entries.append(e)
        prev = [entry_hash(e)]

    known = {entry_hash(e) for e in entries}
    for e in entries:
        for ph in e.parent_hashes:
            assert ph in known


# ── Property 2: Fork detection completeness ────────────────────────────────


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_fork_detection_completeness(num_siblings: int) -> None:
    """N siblings sharing the same parent with distinct content_hashes must
    surface as one fork covering exactly those N siblings."""
    parent = LineageEntry(
        v=1,
        artefact_path="x.py",
        artefact_kind="file",
        content_hash="sha256:" + "a" * 64,
        parent_hashes=[],
        agent_id="agent:t",
        agent_card_kid="k1",
        tool_call_id="tc",
        span_id="span",
        ts_ns=0,
        operator_hmac="00" * 32,
    )
    siblings = [
        LineageEntry(
            v=1,
            artefact_path="x.py",
            artefact_kind="file",
            content_hash="sha256:" + hashlib.sha256(f"sib{i}".encode()).hexdigest(),
            parent_hashes=[entry_hash(parent)],
            agent_id="agent:t",
            agent_card_kid="k1",
            tool_call_id="tc",
            span_id="span",
            ts_ns=i + 1,
            operator_hmac="00" * 32,
        )
        for i in range(num_siblings)
    ]
    forks = detect_forks([parent, *siblings])
    assert len(forks) == 1
    assert set(forks[0].child_hashes) == {entry_hash(s) for s in siblings}


# ── Property 3: Merge resolves siblings ────────────────────────────────────


@given(st.integers(min_value=2, max_value=10))
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_merge_resolves_siblings(num_siblings: int) -> None:
    """A merge entry whose parent_hashes covers all siblings closes the fork:
    every sibling appears in `merged`, and the merge is the only open tip."""
    parent = LineageEntry(
        v=1,
        artefact_path="x.py",
        artefact_kind="file",
        content_hash="sha256:" + "a" * 64,
        parent_hashes=[],
        agent_id="agent:t",
        agent_card_kid="k1",
        tool_call_id="tc",
        span_id="span",
        ts_ns=0,
        operator_hmac="00" * 32,
    )
    siblings = [
        LineageEntry(
            v=1,
            artefact_path="x.py",
            artefact_kind="file",
            content_hash="sha256:" + hashlib.sha256(f"sib{i}".encode()).hexdigest(),
            parent_hashes=[entry_hash(parent)],
            agent_id="agent:t",
            agent_card_kid="k1",
            tool_call_id="tc",
            span_id="span",
            ts_ns=i + 1,
            operator_hmac="00" * 32,
        )
        for i in range(num_siblings)
    ]
    merge = LineageEntry(
        v=1,
        artefact_path="x.py",
        artefact_kind="file",
        content_hash="sha256:" + "f" * 64,
        parent_hashes=[entry_hash(s) for s in siblings],
        agent_id="agent:steward",
        agent_card_kid="ks",
        tool_call_id="tc",
        span_id="span",
        ts_ns=10_000,
        operator_hmac="00" * 32,
    )
    tips = compute_tips([parent, *siblings, merge])
    assert tips["x.py"]["open"] == [entry_hash(merge)]
    assert set(tips["x.py"]["merged"]) == {entry_hash(s) for s in siblings}


# ── Property 4: Signature roundtrip + tamper detection ─────────────────────


@given(st.binary(min_size=1, max_size=256))
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_signature_roundtrip(payload: bytes) -> None:
    jws = sign_detached(payload, _PRIV, kid="k1")
    assert verify_detached(payload, jws, _CARD) is True


@given(st.binary(min_size=1, max_size=128), st.integers(min_value=0))
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_per_byte_tamper_rejected(payload: bytes, flip_seed: int) -> None:
    """Flipping any byte in the signed payload MUST cause verify to fail."""
    assume(len(payload) > 0)
    jws = sign_detached(payload, _PRIV, kid="k1")
    idx = flip_seed % len(payload)
    flipped = bytearray(payload)
    flipped[idx] ^= 0x01
    assert verify_detached(bytes(flipped), jws, _CARD) is False


# ── Property 5: JCS determinism ────────────────────────────────────────────


@given(_entry_strategy(), _entry_strategy())
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_jcs_determinism_same_field_dict_same_bytes(e1: LineageEntry, e2: LineageEntry) -> None:
    """For two entries with identical field values, canonical bytes must
    match - regardless of insertion order during construction."""
    if asdict(e1) == asdict(e2):
        assert canonicalise(e1) == canonicalise(e2)


@given(_entry_strategy())
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_jcs_round_trip_through_json(entry: LineageEntry) -> None:
    """Serialise → deserialise → re-canonicalise must yield identical bytes."""
    raw = canonicalise(entry)
    obj = json.loads(raw)
    rebuilt = LineageEntry(**obj)
    assert canonicalise(rebuilt) == raw


# ── Property 6: HMAC envelope tamper detection per byte flip ───────────────


@given(st.binary(min_size=8, max_size=256), st.integers(min_value=0))
@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_hmac_tamper_per_byte(payload: bytes, flip_seed: int) -> None:
    """Recompute HMAC on payload; flip a byte; new HMAC must differ.

    Models the operator HMAC envelope used in the lineage entry: any
    single-bit tamper of the input is detectable by recomputing.
    """
    secret = b"operator-secret-32-bytes-of-randomness!!"
    import hmac as _hmac

    sig_a = _hmac.new(secret, payload, hashlib.sha256).hexdigest()
    idx = flip_seed % len(payload)
    tampered = bytearray(payload)
    tampered[idx] ^= 0x80
    sig_b = _hmac.new(secret, bytes(tampered), hashlib.sha256).hexdigest()
    assert sig_a != sig_b


# ── Property 7: Genesis entries are open tips ──────────────────────────────


@given(st.integers(min_value=1, max_value=5))
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property_single_entry_is_open_tip(_n: int) -> None:
    e = LineageEntry(
        v=1,
        artefact_path="z.py",
        artefact_kind="file",
        content_hash="sha256:" + "9" * 64,
        parent_hashes=[],
        agent_id="agent:t",
        agent_card_kid="k1",
        tool_call_id="tc",
        span_id="span",
        ts_ns=0,
        operator_hmac="00" * 32,
    )
    tips = compute_tips([e])
    assert tips["z.py"]["open"] == [entry_hash(e)]
