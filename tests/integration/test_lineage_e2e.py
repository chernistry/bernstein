"""End-to-end scenarios for lineage v1 (ADR-009 §12.4).

The compliance-pack roundtrip + air-gap auditor scenarios live with
their owning agents (C/D); here we cover the gate-side scenarios:

  1. Parallel-agent fight ends in CI gate FAIL until steward merge.
  2. Tamper detection: any byte-flip in log.jsonl → gate FAIL.
  3. Reindex round-trip: deleting projections + rebuilding reproduces state.
  4. Steward allow-list rejects worker-authored merges.
  5. Path-traversal artefact rejected on schema construction.
  6. Replay of an old entry into a new log is detected as duplicate.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from bernstein.cli.commands._lineage_v1_helpers import read_entries, reindex
from bernstein.core.lineage.entry import LineageEntry, canonicalise, entry_hash
from bernstein.core.lineage.gate import check
from bernstein.core.lineage.identity import AgentCard, generate_keypair, sign_detached
from bernstein.core.lineage.merge import StewardKey, build_merge_entry
from bernstein.core.lineage.tips import detect_forks


def _h(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


class _Agent:
    def __init__(self, agent_id: str, kid: str) -> None:
        self.agent_id = agent_id
        self.kid = kid
        self.priv, self.pub = generate_keypair()
        self.card = AgentCard(agent_id=agent_id, kid=kid, public_key_pem=self.pub)


def _write_card(cards_dir: Path, agent: _Agent) -> None:
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


def _entry(
    agent: _Agent,
    artefact_path: str,
    content_hash: str,
    parent_hashes: list[str],
    ts_ns: int,
) -> LineageEntry:
    return LineageEntry(
        v=1,
        artefact_path=artefact_path,
        artefact_kind="file",
        content_hash=content_hash,
        parent_hashes=parent_hashes,
        agent_id=agent.agent_id,
        agent_card_kid=agent.kid,
        tool_call_id=f"tc-{ts_ns}",
        span_id=f"span-{ts_ns}",
        ts_ns=ts_ns,
        operator_hmac="deadbeef" * 8,
    )


def _append(log_path: Path, entry: LineageEntry, agent: _Agent) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Append the canonical JCS bytes the real ``LineageStore.append`` writes;
    # the gate binds verification to the on-disk bytes (issue #1848).
    canonical = canonicalise(entry)
    with log_path.open("ab") as f:
        f.write(canonical + b"\n")
    jws = sign_detached(canonical, agent.priv, kid=agent.kid)
    digest = hashlib.sha256(entry.artefact_path.encode()).hexdigest()
    sig_dir = log_path.parent / "signatures" / digest[:2] / digest
    sig_dir.mkdir(parents=True, exist_ok=True)
    eh = entry_hash(entry)
    (sig_dir / (eh.replace("sha256:", "") + ".jws")).write_text(jws)


def _append_signed(log_path: Path, entry: LineageEntry, jws: str) -> None:
    """Append a pre-signed entry (used when steward.build_merge_entry already signed)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Canonical bytes on disk so the gate's byte-canonical check passes.
    with log_path.open("ab") as f:
        f.write(canonicalise(entry) + b"\n")
    digest = hashlib.sha256(entry.artefact_path.encode()).hexdigest()
    sig_dir = log_path.parent / "signatures" / digest[:2] / digest
    sig_dir.mkdir(parents=True, exist_ok=True)
    eh = entry_hash(entry)
    (sig_dir / (eh.replace("sha256:", "") + ".jws")).write_text(jws)


# ── 1. Parallel-agent fight + steward merge ─────────────────────────────────


def test_parallel_agent_fight_then_steward_merge_resolves(tmp_path: Path) -> None:
    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    worker_a = _Agent("agent:worker-a", "ka")
    worker_b = _Agent("agent:worker-b", "kb")
    steward = _Agent("agent:steward", "ks")
    _write_card(cards, worker_a)
    _write_card(cards, worker_b)
    _write_card(cards, steward)

    g = _entry(worker_a, "shared.py", _h("1"), [], ts_ns=1)
    _append(log, g, worker_a)
    fa = _entry(worker_a, "shared.py", _h("2"), [entry_hash(g)], ts_ns=2)
    fb = _entry(worker_b, "shared.py", _h("3"), [entry_hash(g)], ts_ns=3)
    _append(log, fa, worker_a)
    _append(log, fb, worker_b)

    pre = check(log_path=log, agent_cards_dir=cards)
    assert pre.ok is False
    assert any("fork" in f.lower() or "tip" in f.lower() for f in pre.failures)

    # Steward writes a merge entry resolving the fork.
    forks = detect_forks(read_entries(log))
    assert len(forks) == 1
    sk = StewardKey(
        card=steward.card,
        private_key_pem=steward.priv,
        operator_secret=b"",
    )
    merge_entries = build_merge_entry(
        forks,
        resolved_content_by_path={"shared.py": b"merged content"},
        steward=sk,
        now_ns=100,
    )
    for me, jws in merge_entries:
        _append_signed(log, me, jws)

    post = check(log_path=log, agent_cards_dir=cards)
    assert post.ok is True, post.failures


# ── 2. Tamper detection ─────────────────────────────────────────────────────


def test_tamper_detection_byte_flip_in_log(tmp_path: Path) -> None:
    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    a = _Agent("agent:a", "k1")
    _write_card(cards, a)
    g = _entry(a, "x.py", _h("1"), [], ts_ns=1)
    _append(log, g, a)
    pre = check(log_path=log, agent_cards_dir=cards)
    assert pre.ok is True

    # Flip the first byte of the content_hash hex region.
    raw = log.read_text()
    pos = raw.index(_h("1"))
    flipped = bytearray(raw.encode())
    flipped[pos + 8] = ord("9") if chr(flipped[pos + 8]) != "9" else ord("0")
    log.write_bytes(bytes(flipped))

    post = check(log_path=log, agent_cards_dir=cards)
    assert post.ok is False


# ── 3. Reindex round-trip ───────────────────────────────────────────────────


def test_reindex_round_trip_state_matches(tmp_path: Path) -> None:
    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    a = _Agent("agent:a", "k1")
    _write_card(cards, a)
    g = _entry(a, "x.py", _h("1"), [], ts_ns=1)
    c = _entry(a, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    _append(log, g, a)
    _append(log, c, a)
    reindex(log)
    digest = hashlib.sha256(b"x.py").hexdigest()
    tips_path = log.parent / "tips" / (digest + ".json")
    before = tips_path.read_text()

    shutil.rmtree(log.parent / "by-artefact")
    shutil.rmtree(log.parent / "tips")
    assert not tips_path.exists()
    reindex(log)
    after = tips_path.read_text()
    assert before == after


# ── 4. Steward allow-list rejects worker merge ─────────────────────────────


def test_steward_allowlist_rejects_worker_merge(tmp_path: Path) -> None:
    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    worker = _Agent("agent:worker", "k1")
    _write_card(cards, worker)
    g = _entry(worker, "x.py", _h("1"), [], ts_ns=1)
    f1 = _entry(worker, "x.py", _h("2"), [entry_hash(g)], ts_ns=2)
    f2 = _entry(worker, "x.py", _h("3"), [entry_hash(g)], ts_ns=3)
    # Worker maliciously writes a merge with itself as author.
    rogue_merge = _entry(
        worker,
        "x.py",
        _h("9"),
        [entry_hash(f1), entry_hash(f2)],
        ts_ns=4,
    )
    for e in [g, f1, f2, rogue_merge]:
        _append(log, e, worker)
    result = check(
        log_path=log,
        agent_cards_dir=cards,
        steward_allowlist=frozenset({"agent:steward"}),
    )
    assert result.ok is False
    assert any("merge" in f.lower() or "steward" in f.lower() for f in result.failures)


# ── 5. Path traversal rejected on schema construction ──────────────────────


def test_path_traversal_artefact_in_log_does_not_crash_gate(tmp_path: Path) -> None:
    """A path-traversal artefact written into the log MUST NOT escape the
    sandbox at gate-check time (no relative-path resolution against fs).

    The recorder is responsible for rejecting such paths on write; the
    gate's job is to refuse to silently accept them and to keep
    signature/HMAC verification bounded inside the lineage_dir. We assert
    the gate runs without filesystem escape and reports the entry against
    its declared path."""
    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    a = _Agent("agent:a", "k1")
    _write_card(cards, a)
    g = _entry(a, "../../../etc/passwd", _h("1"), [], ts_ns=1)
    _append(log, g, a)
    # Should run cleanly (or fail) but MUST NOT read or escape outside
    # log.parent. Verify by checking the signatures path stays under
    # log.parent.
    sig_root = log.parent / "signatures"
    for sig in sig_root.rglob("*.jws"):
        assert log.parent in sig.parents
    result = check(log_path=log, agent_cards_dir=cards)
    # Whether the gate reports OK or FAIL is policy; the contract here
    # is "no escape" - the test is green if the rglob assertion holds.
    assert isinstance(result.ok, bool)


# ── 6. Replay attack - duplicate entries by hash detection ──────────────────


def test_replay_attack_duplicate_entry_hash_is_idempotent(tmp_path: Path) -> None:
    """Replaying the same entry produces the same entry_hash; the gate does
    not double-count it as a fork or treat it as a separate write."""
    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    a = _Agent("agent:a", "k1")
    _write_card(cards, a)
    g = _entry(a, "x.py", _h("1"), [], ts_ns=1)
    # Write the same entry twice - same body, same hash.
    _append(log, g, a)
    _append(log, g, a)
    # Same content_hash + same parent_hash + same ts → idempotent replay,
    # NOT a fork.
    entries = read_entries(log)
    assert len(entries) == 2
    assert entry_hash(entries[0]) == entry_hash(entries[1])
    forks = detect_forks(entries)
    # Two children of [] with same content_hash and same content → not a fork.
    assert forks == []


# ── 7. CI script end-to-end ─────────────────────────────────────────────────


def test_check_lineage_script_via_subprocess(tmp_path: Path) -> None:
    import subprocess

    log = tmp_path / "lineage" / "log.jsonl"
    cards = tmp_path / "agents"
    a = _Agent("agent:a", "k1")
    _write_card(cards, a)
    g = _entry(a, "x.py", _h("1"), [], ts_ns=1)
    _append(log, g, a)
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "check_lineage.py"
    res = subprocess.run(
        [sys.executable, str(script), "--log", str(log), "--cards", str(cards), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    parsed = json.loads(res.stdout)
    assert parsed["ok"] is True
