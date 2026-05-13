"""Tests for AgentCard + Ed25519 JWS detached sign/verify (RFC 7515 + JWA EdDSA)."""

from __future__ import annotations

import pytest

from bernstein.core.lineage.identity import (
    AgentCard,
    generate_keypair,
    sign_detached,
    verify_detached,
)


def test_sign_verify_roundtrip():
    priv, pub = generate_keypair()
    card = AgentCard(agent_id="agent:test", kid="k1", public_key_pem=pub)
    payload = b"some canonical bytes"
    jws = sign_detached(payload, priv, kid="k1")
    assert verify_detached(payload, jws, card) is True


def test_tampered_payload_fails_verify():
    priv, pub = generate_keypair()
    card = AgentCard(agent_id="agent:test", kid="k1", public_key_pem=pub)
    payload = b"some canonical bytes"
    jws = sign_detached(payload, priv, kid="k1")
    assert verify_detached(b"tampered canonical bytes", jws, card) is False


def test_wrong_kid_fails():
    priv, pub = generate_keypair()
    card = AgentCard(agent_id="agent:test", kid="k1", public_key_pem=pub)
    payload = b"x"
    jws = sign_detached(payload, priv, kid="WRONG")
    assert verify_detached(payload, jws, card) is False


def test_wrong_card_fails():
    priv_a, _pub_a = generate_keypair()
    _priv_b, pub_b = generate_keypair()
    card_b = AgentCard(agent_id="agent:b", kid="k1", public_key_pem=pub_b)
    payload = b"x"
    jws = sign_detached(payload, priv_a, kid="k1")
    assert verify_detached(payload, jws, card_b) is False


def test_jws_compact_form_structure():
    priv, _pub = generate_keypair()
    jws = sign_detached(b"x", priv, kid="k1")
    # Detached: <protected>..<signature>
    parts = jws.split(".")
    assert len(parts) == 3
    assert parts[1] == ""


def test_every_byte_flip_in_payload_fails():
    priv, pub = generate_keypair()
    card = AgentCard("agent:t", "k1", pub)
    payload = b"abcdefghijklmnop"
    jws = sign_detached(payload, priv, kid="k1")
    for i in range(len(payload)):
        flipped = bytearray(payload)
        flipped[i] ^= 0x01
        assert verify_detached(bytes(flipped), jws, card) is False, f"flip at {i}"


def test_every_byte_flip_in_signature_fails():
    priv, pub = generate_keypair()
    card = AgentCard("agent:t", "k1", pub)
    payload = b"some payload"
    jws = sign_detached(payload, priv, kid="k1")
    protected, _, sig = jws.split(".")
    sig_bytes = bytearray(sig.encode("ascii"))
    # Flip one character in the base64 signature — half should still decode,
    # but the underlying bytes change → verify fails.
    failures = 0
    for i in range(len(sig_bytes)):
        flipped = bytearray(sig_bytes)
        # XOR with 0x01 sometimes lands on a non-base64 char; either way
        # the verifier must NOT accept the tampered signature.
        flipped[i] ^= 0x01
        broken = protected + ".." + flipped.decode("ascii", errors="replace")
        result = verify_detached(payload, broken, card)
        if result is False:
            failures += 1
    assert failures == len(sig_bytes)


def test_malformed_jws_rejected():
    _priv, pub = generate_keypair()
    card = AgentCard("agent:t", "k1", pub)
    assert verify_detached(b"x", "not-a-jws", card) is False
    assert verify_detached(b"x", "a.b.c.d", card) is False
    assert verify_detached(b"x", "", card) is False


def test_keypair_is_ed25519():
    priv_pem, pub_pem = generate_keypair()
    assert "PRIVATE KEY" in priv_pem
    assert "PUBLIC KEY" in pub_pem


def test_sign_with_non_ed25519_key_raises(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    rsa = generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = rsa.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    with pytest.raises(TypeError, match="Ed25519"):
        sign_detached(b"x", rsa_pem, kid="k1")
