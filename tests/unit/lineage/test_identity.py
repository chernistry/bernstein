"""Tests for AgentCard + Ed25519 JWS detached sign/verify (RFC 7515 + JWA EdDSA)."""

from __future__ import annotations

import pytest

from bernstein.core.lineage.identity import (
    AgentCard,
    generate_keypair,
    jws_header_kid,
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
    # Flip one character in the base64 signature - half should still decode,
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


# ── jws_header_kid: "Never raises on bad input" contract ────────────────────


def test_jws_header_kid_returns_signed_kid():
    priv, _pub = generate_keypair()
    jws = sign_detached(b"payload", priv, kid="k-7")
    assert jws_header_kid(jws) == "k-7"


def test_jws_header_kid_none_on_wrong_segment_count():
    # Too few segments and 4+ segments both yield None, never an exception.
    assert jws_header_kid("only-one-segment") is None
    assert jws_header_kid("a.b.c.d") is None


def test_jws_header_kid_none_on_non_empty_payload_segment():
    # The middle (payload) segment must be empty in detached form.
    priv, _pub = generate_keypair()
    protected = sign_detached(b"x", priv, kid="k1").split(".", 1)[0]
    assert jws_header_kid(f"{protected}.notempty.AAAA") is None


def test_jws_header_kid_never_raises_on_malformed_base64():
    """``_b64url_decode`` calls ``base64.urlsafe_b64decode``, which raises
    ``binascii.Error`` on malformed base64url. ``binascii.Error`` is a
    ``ValueError`` subclass, so the contract holds, but this pins the "Never
    raises on bad input" guarantee against a hierarchy change or a regression
    that narrows the except clause."""
    # '@' / '!' are outside the base64url alphabet -> decode error inside the
    # protected-header parse. The function must swallow it and return None.
    assert jws_header_kid("@@@bad-base64@@@..AAAA") is None
    assert jws_header_kid("!!!..AAAA") is None


def test_jws_header_kid_none_when_header_not_json():
    import base64

    seg = base64.urlsafe_b64encode(b"this is not json{").rstrip(b"=").decode("ascii")
    assert jws_header_kid(f"{seg}..AAAA") is None


def test_jws_header_kid_none_when_kid_not_a_string():
    import base64
    import json

    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": 12345}).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    assert jws_header_kid(f"{header}..AAAA") is None
