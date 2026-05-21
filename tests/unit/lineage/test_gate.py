"""Tests for the lineage CI gate (ADR-009 §6.2)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from bernstein.core.lineage.entry import LineageEntry, canonicalise, compute_operator_hmac, entry_hash
from bernstein.core.lineage.gate import GateResult, check
from bernstein.core.lineage.identity import AgentCard, generate_keypair, sign_detached

# ── Test fixtures and helpers ───────────────────────────────────────────────


class _TestAgent:
    def __init__(self, agent_id: str, kid: str) -> None:
        self.agent_id = agent_id
        self.kid = kid
        self.priv, self.pub = generate_keypair()
        self.card = AgentCard(agent_id=agent_id, kid=kid, public_key_pem=self.pub)


def _entry_for(
    agent: _TestAgent,
    artefact_path: str,
    content_hash: str,
    parent_hashes: list[str],
    *,
    ts_ns: int = 1_715_600_000_000_000_000,
    operator_secret: bytes = b"op-secret",
) -> LineageEntry:
    unsigned = LineageEntry(
        v=1,
        artefact_path=artefact_path,
        artefact_kind="file",
        content_hash=content_hash,
        parent_hashes=parent_hashes,
        agent_id=agent.agent_id,
        agent_card_kid=agent.kid,
        tool_call_id="tc-x",
        span_id="span-x",
        ts_ns=ts_ns,
        operator_hmac="",
    )
    op_hmac = compute_operator_hmac(unsigned, operator_secret)
    return LineageEntry(
        v=1,
        artefact_path=artefact_path,
        artefact_kind="file",
        content_hash=content_hash,
        parent_hashes=parent_hashes,
        agent_id=agent.agent_id,
        agent_card_kid=agent.kid,
        tool_call_id="tc-x",
        span_id="span-x",
        ts_ns=ts_ns,
        operator_hmac=op_hmac,
    )


def _h(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _write_card(cards_dir: Path, agent: _TestAgent) -> None:
    d = cards_dir / agent.agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "card.json").write_text(
        json.dumps(
            {
                "protocolVersion": "a2a/1.0",
                "agent_id": agent.agent_id,
                "kid": agent.kid,
                "public_key_pem": agent.pub,
            }
        )
    )


def _shard(s: str) -> str:
    # "sha256:abcd..." → first 2 chars after the prefix
    digest = s.split(":", 1)[1]
    return digest[:2]


def _write_log_and_sigs(
    log_path: Path,
    entries: list[tuple[LineageEntry, str | None]],  # (entry, agent_priv_or_none)
    agents_by_id: dict[str, _TestAgent],
) -> None:
    """Write the log.jsonl + per-entry detached JWS sidecars."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sig_root = log_path.parent / "signatures"
    sig_root.mkdir(exist_ok=True)
    with log_path.open("w") as f:
        for entry, _ in entries:
            f.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
    for entry, _ in entries:
        agent = agents_by_id[entry.agent_id]
        canonical = canonicalise(entry)
        jws = sign_detached(canonical, agent.priv, kid=agent.kid)
        eh = entry_hash(entry)
        # Path-shard + entry-hash filename
        path_hash = hashlib.sha256(entry.artefact_path.encode()).hexdigest()
        dest_dir = sig_root / path_hash[:2] / path_hash
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / (eh.replace("sha256:", "") + ".jws")).write_text(jws)


# ── Happy path ──────────────────────────────────────────────────────────────


def test_gate_passes_on_clean_chain(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "src/x.py", _h("1"), [], ts_ns=1)
    c1 = _entry_for(a, "src/x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    _write_log_and_sigs(log, [(g, None), (c1, None)], {a.agent_id: a})
    result = check(log_path=log, agent_cards_dir=cards)
    assert isinstance(result, GateResult)
    assert result.ok is True
    assert result.failures == []


def test_gate_passes_when_log_missing(tmp_path: Path) -> None:
    cards = tmp_path / "agents"
    cards.mkdir()
    # No log written.
    result = check(log_path=tmp_path / "lineage" / "log.jsonl", agent_cards_dir=cards)
    assert result.ok is True


# ── Unresolved fork ─────────────────────────────────────────────────────────


def test_gate_fails_on_unresolved_fork(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(a, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    _write_log_and_sigs(log, [(g, None), (f1, None), (f2, None)], {a.agent_id: a})
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    assert any("unresolved" in f.lower() or "fork" in f.lower() for f in result.failures)


def test_gate_passes_after_merge_resolves_fork(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    s = _TestAgent("agent:steward", "ks")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    _write_card(cards, s)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(a, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    m = _entry_for(s, "x.py", _h("4"), [entry_hash(f1), entry_hash(f2)], ts_ns=4)
    _write_log_and_sigs(
        log,
        [(g, None), (f1, None), (f2, None), (m, None)],
        {a.agent_id: a, s.agent_id: s},
    )
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is True, result.failures


# ── Signature tampering ─────────────────────────────────────────────────────


def test_gate_fails_on_missing_signature(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    _write_log_and_sigs(log, [(g, None)], {a.agent_id: a})
    # Delete the JWS sidecar.
    sig_root = log.parent / "signatures"
    for jws_file in sig_root.rglob("*.jws"):
        jws_file.unlink()
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    assert any("signature" in f.lower() or "missing" in f.lower() for f in result.failures)


def test_gate_fails_on_tampered_entry(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    _write_log_and_sigs(log, [(g, None)], {a.agent_id: a})
    # Tamper with the log: flip a byte in content_hash.
    raw = log.read_text()
    raw_tampered = raw.replace(_h("1"), _h("9"), 1)
    assert raw_tampered != raw
    log.write_text(raw_tampered)
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    assert any("signature" in f.lower() for f in result.failures)


def test_gate_fails_on_tampered_hmac(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    # Build an entry, then write directly with mangled HMAC.
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    # Replace with garbage hmac.
    bad_dict = asdict(g)
    bad_dict["operator_hmac"] = "deadbeef" * 8
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(bad_dict, sort_keys=True) + "\n")
    # Write a valid JWS for the bad entry.
    bad_entry = LineageEntry(**bad_dict)
    canonical = canonicalise(bad_entry)
    jws = sign_detached(canonical, a.priv, kid=a.kid)
    eh = entry_hash(bad_entry)
    path_hash = hashlib.sha256(bad_entry.artefact_path.encode()).hexdigest()
    dest_dir = log.parent / "signatures" / path_hash[:2] / path_hash
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / (eh.replace("sha256:", "") + ".jws")).write_text(jws)
    # Provide the operator secret that won't match.
    result = check(log_path=log, agent_cards_dir=cards, operator_secret=b"op-secret")
    assert result.ok is False
    assert any("hmac" in f.lower() for f in result.failures)


# ── Parent chain integrity ──────────────────────────────────────────────────


def test_gate_fails_on_dangling_parent(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    orphan = _entry_for(a, "x.py", _h("2"), [_h("nonexistent")], ts_ns=2)
    _write_log_and_sigs(log, [(orphan, None)], {a.agent_id: a})
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    assert any("parent" in f.lower() or "chain" in f.lower() for f in result.failures)


# ── Missing Agent Card ──────────────────────────────────────────────────────


def test_gate_fails_when_agent_card_unknown(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    cards.mkdir(parents=True, exist_ok=True)
    # Do NOT write card for `a`.
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    _write_log_and_sigs(log, [(g, None)], {a.agent_id: a})
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    assert any("card" in f.lower() or "unknown agent" in f.lower() for f in result.failures)


# ── Steward allow-list (privilege escalation) ───────────────────────────────


def test_gate_rejects_worker_writing_merge_entry(tmp_path: Path) -> None:
    worker = _TestAgent("agent:worker", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, worker)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(worker, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(worker, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(worker, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    # Worker tries to write a merge entry - must be rejected if allow-list set.
    m = _entry_for(worker, "x.py", _h("4"), [entry_hash(f1), entry_hash(f2)], ts_ns=4)
    _write_log_and_sigs(log, [(g, None), (f1, None), (f2, None), (m, None)], {worker.agent_id: worker})
    result = check(
        log_path=log,
        agent_cards_dir=cards,
        steward_allowlist=frozenset({"agent:steward"}),
    )
    assert result.ok is False
    assert any("merge" in f.lower() or "steward" in f.lower() for f in result.failures)


def test_gate_allows_worker_merge_when_no_allowlist_configured(tmp_path: Path) -> None:
    """Default: no allow-list => no privilege check. Steward role is policy-only."""
    worker = _TestAgent("agent:worker", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, worker)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(worker, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(worker, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(worker, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    m = _entry_for(worker, "x.py", _h("4"), [entry_hash(f1), entry_hash(f2)], ts_ns=4)
    _write_log_and_sigs(log, [(g, None), (f1, None), (f2, None), (m, None)], {worker.agent_id: worker})
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is True


# ── GateResult shape ────────────────────────────────────────────────────────


def test_gate_result_is_tuple_like(tmp_path: Path) -> None:
    cards = tmp_path / "agents"
    cards.mkdir()
    result = check(log_path=tmp_path / "noop.jsonl", agent_cards_dir=cards)
    assert hasattr(result, "ok")
    assert hasattr(result, "failures")
    assert isinstance(result.failures, list)


def test_gate_handles_corrupt_log_line(tmp_path: Path) -> None:
    cards = tmp_path / "agents"
    cards.mkdir()
    log = tmp_path / "lineage" / "log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("{not-json\n")
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    assert any("parse" in f.lower() or "corrupt" in f.lower() for f in result.failures)


def test_gate_handles_card_directory_missing(tmp_path: Path) -> None:
    log = tmp_path / "lineage" / "log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    # No agents dir.
    result = check(log_path=log, agent_cards_dir=tmp_path / "no-such-dir")
    assert result.ok is True  # empty log → OK regardless of cards dir


# ── Property-style: HMAC matches when operator_secret given ────────────────


def test_gate_verifies_hmac_when_secret_provided(tmp_path: Path) -> None:
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1, operator_secret=b"op-secret")
    _write_log_and_sigs(log, [(g, None)], {a.agent_id: a})
    # Wrong operator secret → HMAC mismatch.
    bad = check(log_path=log, agent_cards_dir=cards, operator_secret=b"WRONG")
    assert bad.ok is False
    # Right operator secret → OK.
    good = check(log_path=log, agent_cards_dir=cards, operator_secret=b"op-secret")
    assert good.ok is True, good.failures


def test_gate_accepts_recorder_emitted_entries_under_operator_secret(tmp_path: Path) -> None:
    """Regression: recorder and gate must agree on the operator-HMAC body.

    Previously the gate computed HMAC over a 3-field body (``{p, h, ts}``)
    while the recorder used the full JCS-canonical entry body with the
    ``operator_hmac`` field blanked. Production gate runs against real
    recorder output failed 100% of entries. Pin the agreement end-to-end.
    """
    from bernstein.core.lineage.identity import generate_keypair
    from bernstein.core.lineage.recorder import LineageRecorder
    from bernstein.core.lineage.store import LineageStore

    priv, pub = generate_keypair()
    card = AgentCard(agent_id="agent:rec-1", kid="kid-rec-1", public_key_pem=pub)
    # Write the card to disk so the gate can resolve the signing key.
    cards_dir = tmp_path / "agents"
    (cards_dir / card.agent_id).mkdir(parents=True)
    (cards_dir / card.agent_id / "card.json").write_text(
        json.dumps(
            {
                "protocolVersion": "a2a/1.0",
                "agent_id": card.agent_id,
                "kid": card.kid,
                "public_key_pem": pub,
            }
        )
    )

    operator_secret = b"recorder-gate-shared-secret"
    store = LineageStore(tmp_path / "lineage")
    recorder = LineageRecorder(store=store, operator_hmac_key=operator_secret)
    recorder.record_write(
        artefact_path="src/foo.py",
        new_content=b"hello",
        agent_id=card.agent_id,
        agent_card=card,
        private_key_pem=priv,
        tool_call_id="tc-1",
        span_id="span-1",
    )

    log_path = tmp_path / "lineage" / "log.jsonl"
    # Right secret → gate accepts the recorder's entry.
    good = check(log_path=log_path, agent_cards_dir=cards_dir, operator_secret=operator_secret)
    assert good.ok is True, good.failures
    # Wrong secret → gate flags HMAC mismatch.
    bad = check(log_path=log_path, agent_cards_dir=cards_dir, operator_secret=b"different-secret")
    assert bad.ok is False
    assert any("HMAC mismatch" in f for f in bad.failures), bad.failures


# ── Mutation-killing tests (close survivor gaps) ────────────────────────────


def test_gate_rejects_malformed_agent_card_with_non_string_field(tmp_path: Path) -> None:
    """`_load_cards` must skip cards where any of agent_id/kid/public_key_pem
    is not a string. Kills the `and -> or` mutation in the isinstance chain."""
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    card_dir = cards / "agent:bad"
    card_dir.mkdir(parents=True, exist_ok=True)
    (card_dir / "card.json").write_text(
        json.dumps(
            {
                "protocolVersion": "a2a/1.0",
                "agent_id": "agent:bad",
                "kid": None,  # ← invalid; should cause card to be skipped
                "public_key_pem": a.pub,
            }
        )
    )
    # Also a valid card for agent:a so we can issue a real entry.
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    _write_log_and_sigs(log, [(g, None)], {a.agent_id: a})
    # The bad card was skipped, but agent:a is fine → gate passes.
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is True
    # Now write an entry from agent:bad with no valid card → gate fails.
    bad = _TestAgent("agent:bad", "k1")
    g2 = _entry_for(bad, "y.py", _h("2"), [], ts_ns=2)
    _write_log_and_sigs(log, [(g, None), (g2, None)], {a.agent_id: a, bad.agent_id: bad})
    result2 = check(log_path=log, agent_cards_dir=cards)
    assert result2.ok is False
    assert any("agent:bad" in f and ("card" in f.lower() or "unknown" in f.lower()) for f in result2.failures)


def test_gate_steward_allowlist_does_not_check_non_merge_entries(tmp_path: Path) -> None:
    """The steward allow-list only applies when parent_hashes length >= 2.
    A genesis or single-parent entry from a non-allowed agent must NOT
    trigger the privilege check. Kills the `>= 2 -> >= 1` mutation."""
    a = _TestAgent("agent:worker", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    c1 = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    _write_log_and_sigs(log, [(g, None), (c1, None)], {a.agent_id: a})
    # Allowlist excludes "agent:worker", but no merge entries exist → pass.
    result = check(
        log_path=log,
        agent_cards_dir=cards,
        steward_allowlist=frozenset({"agent:steward"}),
    )
    assert result.ok is True, result.failures


def test_gate_failure_count_appears_in_failure_message(tmp_path: Path) -> None:
    """The open-tip failure message must contain the actual count, not zero.
    Kills the `len( -> 0 * len(` mutation."""
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(a, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    f3 = _entry_for(a, "x.py", _h("4"), [entry_hash(g)], ts_ns=4)
    _write_log_and_sigs(
        log,
        [(g, None), (f1, None), (f2, None), (f3, None)],
        {a.agent_id: a},
    )
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    # Match exact count "3 unresolved" - kills the zero-count mutation.
    tip_msgs = [f for f in result.failures if "unresolved" in f and "tips" in f]
    assert tip_msgs, result.failures
    assert "3" in tip_msgs[0]


def test_gate_fork_resolved_flag_initial_false_not_true(tmp_path: Path) -> None:
    """The `resolved = False` initialisation must be False, not True, so that
    forks without a covering merge entry are reported. Kills the
    `False -> True` flip on the resolved sentinel.

    We construct a scenario where the open-tip count is OK (1) but a
    historical fork is still unresolved - that forces the gate to rely on
    the resolved=False initial value.

    Concretely: after a merge, write another genesis-style child of the
    pre-merge state. The merge is the open tip; the fork is technically
    closed in compute_tips terms, but detect_forks still surfaces it.
    Easier test: use the existing unresolved-fork shape and check the
    fork-level message appears alongside the tip-level one.
    """
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(a, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    _write_log_and_sigs(log, [(g, None), (f1, None), (f2, None)], {a.agent_id: a})
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    # The gate must produce a fork-level message too - both classes of
    # message must appear if the initial `resolved = False` is intact.
    fork_msgs = [f for f in result.failures if "unresolved fork" in f]
    assert fork_msgs, f"no fork-level message: {result.failures}"


def test_gate_dangling_parent_hash_count_matches_input(tmp_path: Path) -> None:
    """Multiple dangling parents must each produce a distinct failure message."""
    a = _TestAgent("agent:a", "k1")
    cards = tmp_path / "agents"
    _write_card(cards, a)
    log = tmp_path / "lineage" / "log.jsonl"
    o1 = _entry_for(a, "x.py", _h("2"), [_h("ghost-1")], ts_ns=2)
    o2 = _entry_for(a, "y.py", _h("3"), [_h("ghost-2"), _h("ghost-3")], ts_ns=3)
    _write_log_and_sigs(log, [(o1, None), (o2, None)], {a.agent_id: a})
    result = check(log_path=log, agent_cards_dir=cards)
    assert result.ok is False
    dangling = [f for f in result.failures if "dangling parent_hash" in f]
    # o1 has 1 ghost, o2 has 2 ghosts → 3 dangling-parent messages.
    assert len(dangling) == 3


def test_compute_tips_distinguishes_open_vs_merged(tmp_path: Path) -> None:
    """Ensure compute_tips classifies merge parents as 'merged', not 'open'.
    Kills mutations that confuse the two sets in tips.py."""
    from bernstein.core.lineage.tips import compute_tips

    a = _TestAgent("agent:a", "k1")
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry_for(a, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    m = _entry_for(a, "x.py", _h("4"), [entry_hash(f1), entry_hash(f2)], ts_ns=4)
    tips = compute_tips([g, f1, f2, m])
    assert entry_hash(f1) not in tips["x.py"]["open"]
    assert entry_hash(f2) not in tips["x.py"]["open"]
    assert entry_hash(m) in tips["x.py"]["open"]
    assert entry_hash(f1) in tips["x.py"]["merged"]
    assert entry_hash(f2) in tips["x.py"]["merged"]


def test_detect_forks_requires_at_least_two_children(tmp_path: Path) -> None:
    """A parent with one child must NOT be reported as a fork.
    Kills the `< 2 -> < 1` mutation in detect_forks."""
    from bernstein.core.lineage.tips import detect_forks

    a = _TestAgent("agent:a", "k1")
    g = _entry_for(a, "x.py", _h("1"), [], ts_ns=1)
    only_child = _entry_for(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    assert detect_forks([g, only_child]) == []
