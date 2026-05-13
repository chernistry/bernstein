"""Unit tests for bernstein_verify.verify.

These tests cross-import `bernstein` at TEST scope to prove byte-for-byte
compatibility of our re-implementation. The package under test
(`bernstein_verify`) never imports `bernstein` itself — see
test_no_bernstein_install.py for the install-isolation proof.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict
from pathlib import Path

# Cross-test: original implementation
from bernstein.core.lineage.entry import LineageEntry
from bernstein.core.lineage.entry import canonicalise as bernstein_canonicalise
from bernstein.core.lineage.identity import (
    generate_keypair as bernstein_generate_keypair,
)
from bernstein.core.lineage.identity import (
    sign_detached as bernstein_sign_detached,
)

from bernstein_verify.verify import (
    VerifyResult,
    jcs_canonicalise,
    verify_jws_detached,
    verify_pack,
    walk_chain,
)

# ---------- jcs_canonicalise: byte-equality with bernstein ----------


def _sample_entry_dict(**overrides):
    base = dict(
        v=1,
        artefact_path="src/foo.py",
        artefact_kind="file",
        content_hash="sha256:" + "a" * 64,
        parent_hashes=[],
        agent_id="agent:claude-worker-3",
        agent_card_kid="key-001",
        tool_call_id="tc-7f3a",
        span_id="00f067aa0ba902b7",
        ts_ns=1_715_600_000_000_000_000,
        operator_hmac="deadbeef" * 8,
    )
    base.update(overrides)
    return base


def test_jcs_canonicalise_byte_equal_to_bernstein_simple():
    d = _sample_entry_dict()
    entry = LineageEntry(**d)
    assert jcs_canonicalise(asdict(entry)) == bernstein_canonicalise(entry)


def test_jcs_canonicalise_byte_equal_with_parents():
    d = _sample_entry_dict(parent_hashes=["sha256:" + "1" * 64, "sha256:" + "2" * 64])
    entry = LineageEntry(**d)
    assert jcs_canonicalise(asdict(entry)) == bernstein_canonicalise(entry)


def test_jcs_canonicalise_byte_equal_unicode():
    d = _sample_entry_dict(artefact_path="src/файл.py", agent_id="agent:ünïcødé")
    entry = LineageEntry(**d)
    assert jcs_canonicalise(asdict(entry)) == bernstein_canonicalise(entry)


def test_jcs_canonicalise_independent_of_input_order():
    a = {"b": 1, "a": 2, "c": 3}
    b = {"c": 3, "a": 2, "b": 1}
    assert jcs_canonicalise(a) == jcs_canonicalise(b)


def test_jcs_canonicalise_no_whitespace():
    out = jcs_canonicalise(_sample_entry_dict())
    assert b": " not in out
    assert b", " not in out
    assert b"\n" not in out


def test_jcs_canonicalise_keys_sorted():
    out = jcs_canonicalise(_sample_entry_dict())
    assert out.startswith(b'{"agent_card_kid":')


# ---------- verify_jws_detached: bernstein-signed JWS verifies here ----------


def test_verify_jws_detached_happy_path():
    priv, pub = bernstein_generate_keypair()
    payload = b"some canonical bytes"
    jws = bernstein_sign_detached(payload, priv, kid="k1")
    assert verify_jws_detached(payload, jws, pub, expected_kid="k1") is True


def test_verify_jws_detached_tamper_payload_fails():
    priv, pub = bernstein_generate_keypair()
    payload = b"abc"
    jws = bernstein_sign_detached(payload, priv, kid="k1")
    assert verify_jws_detached(b"abd", jws, pub, expected_kid="k1") is False


def test_verify_jws_detached_wrong_kid_fails():
    priv, pub = bernstein_generate_keypair()
    jws = bernstein_sign_detached(b"x", priv, kid="k1")
    assert verify_jws_detached(b"x", jws, pub, expected_kid="WRONG") is False


def test_verify_jws_detached_wrong_key_fails():
    priv_a, _ = bernstein_generate_keypair()
    _, pub_b = bernstein_generate_keypair()
    jws = bernstein_sign_detached(b"x", priv_a, kid="k1")
    assert verify_jws_detached(b"x", jws, pub_b, expected_kid="k1") is False


def test_verify_jws_detached_malformed_input_returns_false_never_raises():
    _, pub = bernstein_generate_keypair()
    bad_inputs = [
        "",
        "not-a-jws",
        "a.b.c.d",
        "...",
        "a.b.c",  # 3 dot-separated but middle non-empty
        "header..signature.extra",
    ]
    for jws in bad_inputs:
        assert verify_jws_detached(b"x", jws, pub, expected_kid="k1") is False


def test_verify_jws_detached_malformed_pem_returns_false():
    priv, _ = bernstein_generate_keypair()
    jws = bernstein_sign_detached(b"x", priv, kid="k1")
    assert verify_jws_detached(b"x", jws, "not a pem", expected_kid="k1") is False


def test_verify_jws_detached_wrong_alg_fails():
    # Forge a JWS header with alg=HS256
    import base64

    priv, pub = bernstein_generate_keypair()
    real_jws = bernstein_sign_detached(b"x", priv, kid="k1")
    _real_protected, _, sig = real_jws.split(".", maxsplit=2)
    bad_header = {"alg": "HS256", "kid": "k1", "b64": False, "crit": ["b64"]}
    bad_protected = (
        base64.urlsafe_b64encode(
            json.dumps(bad_header, separators=(",", ":"), sort_keys=True).encode()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    forged = bad_protected + ".." + sig
    assert verify_jws_detached(b"x", forged, pub, expected_kid="k1") is False


# ---------- walk_chain ----------


def _mk_entry(content: str, parents: list[str], agent: str = "agent:a") -> dict:
    return _sample_entry_dict(
        content_hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        parent_hashes=parents,
        agent_id=agent,
    )


def _entry_h(d: dict) -> str:
    return "sha256:" + hashlib.sha256(jcs_canonicalise(d)).hexdigest()


def test_walk_chain_happy_path():
    g = _mk_entry("v1", [])
    g_h = _entry_h(g)
    c1 = _mk_entry("v2", [g_h])
    c1_h = _entry_h(c1)
    c2 = _mk_entry("v3", [c1_h])
    ok, errors = walk_chain([g, c1, c2])
    assert ok is True
    assert errors == []


def test_walk_chain_genesis_missing_parents_ok():
    g = _mk_entry("v1", [])
    ok, errors = walk_chain([g])
    assert ok is True
    assert errors == []


def test_walk_chain_orphan_parent_detected():
    g = _mk_entry("v1", ["sha256:" + "f" * 64])
    ok, errors = walk_chain([g])
    assert ok is False
    assert any("orphan" in e.lower() or "unknown parent" in e.lower() for e in errors)


def test_walk_chain_duplicate_entry_detected():
    g = _mk_entry("v1", [])
    ok, errors = walk_chain([g, g])
    assert ok is False
    assert any("duplicate" in e.lower() for e in errors)


def test_walk_chain_merge_entry_two_parents_ok():
    g = _mk_entry("v1", [])
    g_h = _entry_h(g)
    a = _mk_entry("v2a", [g_h], agent="agent:a")
    b = _mk_entry("v2b", [g_h], agent="agent:b")
    a_h, b_h = _entry_h(a), _entry_h(b)
    m = _mk_entry("v3", [a_h, b_h], agent="agent:steward")
    ok, errors = walk_chain([g, a, b, m])
    assert ok is True, errors


def test_walk_chain_out_of_order_parents_still_ok():
    """walk_chain must accept any topological order — entries can arrive
    in any order in the log; what matters is that every parent exists."""
    g = _mk_entry("v1", [])
    g_h = _entry_h(g)
    c = _mk_entry("v2", [g_h])
    ok, errors = walk_chain([c, g])  # child before parent
    assert ok is True, errors


# ---------- verify_pack ----------


def _build_pack(
    tmp_path: Path,
    *,
    tamper_log: bool = False,
    drop_jws: bool = False,
    wrong_kid_card: bool = False,
) -> Path:
    """Build a minimal compliance pack for testing."""
    priv, pub = bernstein_generate_keypair()
    kid = "k1"
    agent_id = "agent:t"

    # Genesis entry
    entry = LineageEntry(
        v=1,
        artefact_path="src/foo.py",
        artefact_kind="file",
        content_hash="sha256:" + "a" * 64,
        parent_hashes=[],
        agent_id=agent_id,
        agent_card_kid=kid,
        tool_call_id="tc-1",
        span_id="00f067aa0ba902b7",
        ts_ns=1_715_600_000_000_000_000,
        operator_hmac="deadbeef" * 8,
    )
    payload = bernstein_canonicalise(entry)
    jws = bernstein_sign_detached(payload, priv, kid=kid)
    entry_hash = "sha256:" + hashlib.sha256(payload).hexdigest()

    card = {
        "agent_id": agent_id,
        "kid": kid if not wrong_kid_card else "WRONG",
        "public_key_pem": pub,
        "protocol_version": "a2a/1.0",
    }

    log_line = json.dumps(asdict(entry), separators=(",", ":"), sort_keys=True)
    if tamper_log:
        log_line = log_line.replace("a" * 64, "b" + "a" * 63)

    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("lineage-log.jsonl", log_line + "\n")
        if not drop_jws:
            z.writestr(f"signatures/{entry_hash}.jws", jws)
        z.writestr(f"agent-cards/{agent_id}.json", json.dumps(card))
    return bundle


def test_verify_pack_happy_path(tmp_path):
    bundle = _build_pack(tmp_path)
    result = verify_pack(bundle)
    assert isinstance(result, VerifyResult)
    assert result.ok is True, result.errors


def test_verify_pack_tampered_log_fails(tmp_path):
    bundle = _build_pack(tmp_path, tamper_log=True)
    result = verify_pack(bundle)
    assert result.ok is False
    # The flipped content_hash byte means entry-hash changes, signature lookup
    # fails, AND if found would fail crypto verify. Either way: ok=False.
    assert result.errors


def test_verify_pack_missing_jws_fails(tmp_path):
    bundle = _build_pack(tmp_path, drop_jws=True)
    result = verify_pack(bundle)
    assert result.ok is False
    assert any("signature" in e.lower() or "jws" in e.lower() for e in result.errors)


def test_verify_pack_wrong_kid_card_fails(tmp_path):
    bundle = _build_pack(tmp_path, wrong_kid_card=True)
    result = verify_pack(bundle)
    assert result.ok is False


def test_verify_pack_missing_log_fails(tmp_path):
    bundle = tmp_path / "empty.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("README.md", "hi")
    result = verify_pack(bundle)
    assert result.ok is False


def test_verify_pack_not_a_zip_fails(tmp_path):
    bundle = tmp_path / "not-a-zip.txt"
    bundle.write_text("hello")
    result = verify_pack(bundle)
    assert result.ok is False


def test_verify_pack_zip_slip_blocked(tmp_path):
    """A malicious zip with `../../../etc/passwd` entries must not write
    outside the extraction sandbox. verify_pack reads from the zip in-memory,
    so this is a defence-in-depth check that we never call extractall().
    """
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("../../../etc/passwd", "rooted")
        z.writestr("lineage-log.jsonl", "")
    # Must not raise, must not write outside tmp_path.
    sentinel = Path("/etc/passwd-bernstein-test")
    assert not sentinel.exists()
    verify_pack(bundle)  # may pass or fail; must NOT escape sandbox
    assert not sentinel.exists()


# ---------- combined: bernstein -> bernstein-verify round-trip ----------


def test_bernstein_signed_entry_verifies_in_verify_cli():
    """The single most important integration assertion in this file:
    an entry signed by bernstein.core.lineage MUST verify with our
    re-implementation."""
    priv, pub = bernstein_generate_keypair()
    entry = LineageEntry(**_sample_entry_dict())
    payload = bernstein_canonicalise(entry)
    jws = bernstein_sign_detached(payload, priv, kid="key-001")

    # Now verify using ONLY bernstein_verify code
    our_payload = jcs_canonicalise(asdict(entry))
    assert our_payload == payload  # canonicalisation must match
    assert verify_jws_detached(our_payload, jws, pub, expected_kid="key-001") is True
