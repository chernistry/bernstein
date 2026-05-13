"""Tests for merge policy resolution + merge entry construction (ADR-009 §6.3)."""

from __future__ import annotations

import hashlib

import pytest

from bernstein.core.lineage.entry import LineageEntry, entry_hash
from bernstein.core.lineage.identity import AgentCard, generate_keypair
from bernstein.core.lineage.merge import (
    AgentPolicy,
    FirstWriterPolicy,
    HumanPolicy,
    LineageConflict,
    MergePolicy,
    StewardKey,
    build_merge_entry,
    resolve_policy,
)
from bernstein.core.lineage.tips import Fork


def _mk(
    artefact_path: str,
    content_hash: str,
    parent_hashes: list[str],
    *,
    ts_ns: int = 1_715_600_000_000_000_000,
    agent_id: str = "agent:a",
) -> LineageEntry:
    return LineageEntry(
        v=1,
        artefact_path=artefact_path,
        artefact_kind="file",
        content_hash=content_hash,
        parent_hashes=parent_hashes,
        agent_id=agent_id,
        agent_card_kid="k1",
        tool_call_id="tc-x",
        span_id="span-x",
        ts_ns=ts_ns,
        operator_hmac="deadbeef" * 8,
    )


def _h(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


# ── resolve_policy ──────────────────────────────────────────────────────────


def test_resolve_human_default() -> None:
    p = resolve_policy("human")
    assert isinstance(p, HumanPolicy)


def test_resolve_first_writer() -> None:
    p = resolve_policy("first-writer")
    assert isinstance(p, FirstWriterPolicy)


def test_resolve_agent_specific() -> None:
    p = resolve_policy("agent:reviewer-bot")
    assert isinstance(p, AgentPolicy)
    assert p.agent_id == "agent:reviewer-bot"


def test_resolve_agent_with_full_slug() -> None:
    p = resolve_policy("agent:agent:reviewer-bot")
    assert isinstance(p, AgentPolicy)
    # "agent:" prefix stripped once → "agent:reviewer-bot"
    assert p.agent_id == "agent:reviewer-bot"


def test_resolve_unknown_policy_raises() -> None:
    with pytest.raises(ValueError, match="unknown merge policy"):
        resolve_policy("magic-policy")


# ── HumanPolicy ─────────────────────────────────────────────────────────────


def test_human_policy_emits_conflict_event() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    f1 = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=2, agent_id="agent:a")
    f2 = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=3, agent_id="agent:b")
    fork = Fork(
        artefact_path="a.py",
        parent_hash=entry_hash(g),
        child_hashes=(entry_hash(f1), entry_hash(f2)),
    )
    policy = HumanPolicy()
    with pytest.raises(LineageConflict) as ei:
        policy.resolve(fork, {entry_hash(f1): f1, entry_hash(f2): f2})
    assert ei.value.artefact_path == "a.py"
    assert set(ei.value.candidate_hashes) == {entry_hash(f1), entry_hash(f2)}


# ── FirstWriterPolicy ───────────────────────────────────────────────────────


def test_first_writer_picks_earliest_ts() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    f_late = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=200, agent_id="agent:a")
    f_early = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=100, agent_id="agent:b")
    fork = Fork(
        artefact_path="a.py",
        parent_hash=entry_hash(g),
        child_hashes=(entry_hash(f_late), entry_hash(f_early)),
    )
    winner = FirstWriterPolicy().resolve(fork, {entry_hash(f_late): f_late, entry_hash(f_early): f_early})
    assert winner == f_early


def test_first_writer_lex_tiebreak_on_agent_id() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    fa = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=100, agent_id="agent:b")
    fb = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=100, agent_id="agent:a")
    fork = Fork(
        artefact_path="a.py",
        parent_hash=entry_hash(g),
        child_hashes=(entry_hash(fa), entry_hash(fb)),
    )
    winner = FirstWriterPolicy().resolve(fork, {entry_hash(fa): fa, entry_hash(fb): fb})
    assert winner.agent_id == "agent:a"


# ── AgentPolicy ─────────────────────────────────────────────────────────────


def test_agent_policy_picks_designated() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    fa = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=100, agent_id="agent:worker")
    fb = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=200, agent_id="agent:reviewer")
    fork = Fork(
        artefact_path="a.py",
        parent_hash=entry_hash(g),
        child_hashes=(entry_hash(fa), entry_hash(fb)),
    )
    winner = AgentPolicy("agent:reviewer").resolve(fork, {entry_hash(fa): fa, entry_hash(fb): fb})
    assert winner == fb


def test_agent_policy_missing_designated_raises() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    fa = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=100, agent_id="agent:worker")
    fb = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=200, agent_id="agent:other")
    fork = Fork(
        artefact_path="a.py",
        parent_hash=entry_hash(g),
        child_hashes=(entry_hash(fa), entry_hash(fb)),
    )
    with pytest.raises(LineageConflict, match="no tip from designated"):
        AgentPolicy("agent:reviewer").resolve(fork, {entry_hash(fa): fa, entry_hash(fb): fb})


# ── build_merge_entry ───────────────────────────────────────────────────────


def _steward_key() -> StewardKey:
    priv, pub = generate_keypair()
    card = AgentCard(agent_id="agent:steward", kid="steward-1", public_key_pem=pub)
    return StewardKey(card=card, private_key_pem=priv)


def test_build_merge_entry_creates_single_entry_for_one_fork() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    f1 = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=2, agent_id="agent:a")
    f2 = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=3, agent_id="agent:b")
    forks = [
        Fork(
            artefact_path="a.py",
            parent_hash=entry_hash(g),
            child_hashes=(entry_hash(f1), entry_hash(f2)),
        )
    ]
    resolved_content = b"merged file content"
    sk = _steward_key()
    entries = build_merge_entry(
        forks,
        resolved_content_by_path={"a.py": resolved_content},
        steward=sk,
        now_ns=1_000,
    )
    assert len(entries) == 1
    me, jws = entries[0]
    assert me.artefact_path == "a.py"
    assert set(me.parent_hashes) == {entry_hash(f1), entry_hash(f2)}
    assert me.agent_id == "agent:steward"
    assert me.agent_card_kid == "steward-1"
    assert me.ts_ns == 1_000
    expected = "sha256:" + hashlib.sha256(resolved_content).hexdigest()
    assert me.content_hash == expected
    # JWS not empty
    assert jws.startswith("e") and ".." in jws


def test_build_merge_entry_multiple_forks_multiple_entries() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    fa1 = _mk("a.py", _h("2"), [entry_hash(g)], ts_ns=2)
    fa2 = _mk("a.py", _h("3"), [entry_hash(g)], ts_ns=3)
    h = _mk("b.py", _h("4"), [], ts_ns=1)
    fb1 = _mk("b.py", _h("5"), [entry_hash(h)], ts_ns=2)
    fb2 = _mk("b.py", _h("6"), [entry_hash(h)], ts_ns=3)
    forks = [
        Fork("a.py", entry_hash(g), (entry_hash(fa1), entry_hash(fa2))),
        Fork("b.py", entry_hash(h), (entry_hash(fb1), entry_hash(fb2))),
    ]
    sk = _steward_key()
    entries = build_merge_entry(
        forks,
        resolved_content_by_path={"a.py": b"A", "b.py": b"B"},
        steward=sk,
        now_ns=1_000,
    )
    assert len(entries) == 2
    paths = {e.artefact_path for e, _ in entries}
    assert paths == {"a.py", "b.py"}


def test_build_merge_entry_missing_content_raises() -> None:
    g = _mk("a.py", _h("1"), [], ts_ns=1)
    f1 = _mk("a.py", _h("2"), [entry_hash(g)])
    f2 = _mk("a.py", _h("3"), [entry_hash(g)])
    forks = [Fork("a.py", entry_hash(g), (entry_hash(f1), entry_hash(f2)))]
    sk = _steward_key()
    with pytest.raises(KeyError):
        build_merge_entry(forks, resolved_content_by_path={}, steward=sk, now_ns=1_000)


def test_merge_policy_protocol() -> None:
    """All concrete policies satisfy the MergePolicy protocol."""
    assert isinstance(HumanPolicy(), MergePolicy)
    assert isinstance(FirstWriterPolicy(), MergePolicy)
    assert isinstance(AgentPolicy("x"), MergePolicy)
