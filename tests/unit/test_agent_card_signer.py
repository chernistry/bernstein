"""Tests for ``agent_card_signer`` — JWS over JCS, EdDSA."""

from __future__ import annotations

import json

import pytest

from bernstein.core.security.agent_card_signer import (
    AgentCardSignature,
    canonicalize_jcs,
    generate_ed25519_keypair,
    sign_agent_card,
    verify_agent_card,
)
from bernstein.core.security.agent_identity import (
    AgentIdentityCard,
    issue_identity_card,
)

# ---------------------------------------------------------------------------
# JCS canonicalization
# ---------------------------------------------------------------------------


class TestCanonicalizeJCS:
    def test_object_key_order_is_lexicographic(self) -> None:
        a = canonicalize_jcs({"b": 1, "a": 2, "c": 3})
        b = canonicalize_jcs({"a": 2, "c": 3, "b": 1})
        assert a == b == b'{"a":2,"b":1,"c":3}'

    def test_no_whitespace_separators(self) -> None:
        out = canonicalize_jcs({"a": 1, "b": [1, 2, 3]})
        assert b": " not in out
        assert b", " not in out

    def test_nested_objects_are_canonicalized(self) -> None:
        a = canonicalize_jcs({"outer": {"z": 1, "a": 2}})
        b = canonicalize_jcs({"outer": {"a": 2, "z": 1}})
        assert a == b

    def test_unicode_emitted_as_utf8(self) -> None:
        # ensure_ascii=False so we sign actual UTF-8 bytes, matching RFC 8785.
        out = canonicalize_jcs({"k": "værsågod"})
        assert "værsågod".encode() in out

    def test_nan_and_infinity_rejected(self) -> None:
        with pytest.raises(ValueError):
            canonicalize_jcs({"x": float("nan")})


# ---------------------------------------------------------------------------
# Keypair generation
# ---------------------------------------------------------------------------


class TestKeypair:
    def test_generates_pem_pair(self) -> None:
        priv, pub = generate_ed25519_keypair()
        assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")

    def test_keys_are_independent(self) -> None:
        a_priv, a_pub = generate_ed25519_keypair()
        b_priv, b_pub = generate_ed25519_keypair()
        assert a_priv != b_priv
        assert a_pub != b_pub


# ---------------------------------------------------------------------------
# Sign / verify round-trip
# ---------------------------------------------------------------------------


def _sample_card() -> AgentIdentityCard:
    return issue_identity_card(
        agent_id="claude-security-test123",
        role="security",
        adapter="claude-cli",
        model="claude-opus-4-7",
        scope=["src/", "tests/"],
        max_budget_usd=5.0,
        ttl_seconds=3600,
    )


class TestSignVerify:
    def test_round_trip_succeeds(self) -> None:
        priv, pub = generate_ed25519_keypair()
        card = _sample_card()

        sig = sign_agent_card(card, priv)
        assert verify_agent_card(card, sig, pub) is True

    def test_signature_is_detached(self) -> None:
        priv, _ = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv)

        # RFC 7515 §A.5: detached → empty middle segment.
        parts = sig.detached_jws.split(".")
        assert len(parts) == 3
        assert parts[1] == ""

    def test_kid_default_includes_agent_id(self) -> None:
        priv, _ = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv)
        assert sig.kid == "agent-claude-security-test123"

    def test_kid_override(self) -> None:
        priv, _ = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv, kid="bernstein-prod-2026-01")
        assert sig.kid == "bernstein-prod-2026-01"

    def test_alg_is_eddsa(self) -> None:
        sig = AgentCardSignature(detached_jws="x..y", kid="k")
        assert sig.alg == "EdDSA"

    def test_tampered_card_fails_verification(self) -> None:
        priv, pub = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv)

        tampered = _sample_card()
        tampered.max_budget_usd = 9999.0  # privilege escalation attempt
        assert verify_agent_card(tampered, sig, pub) is False

    def test_wrong_public_key_fails_verification(self) -> None:
        priv, _ = generate_ed25519_keypair()
        _, other_pub = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv)
        assert verify_agent_card(card, sig, other_pub) is False

    def test_malformed_jws_returns_false(self) -> None:
        _, pub = generate_ed25519_keypair()
        card = _sample_card()
        # Only two segments instead of three.
        bad = AgentCardSignature(detached_jws="abc.def", kid="k")
        assert verify_agent_card(card, bad, pub) is False

    def test_non_detached_payload_rejected(self) -> None:
        """Refuse signatures whose payload segment isn't empty.

        A non-detached JWS would let an attacker substitute a payload that
        differs from the card body the verifier sees. We explicitly require
        the empty middle segment.
        """
        priv, pub = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv)
        header_b64, _empty, sig_b64 = sig.detached_jws.split(".")
        # Inject a payload claim where there should be none.
        forged = AgentCardSignature(
            detached_jws=f"{header_b64}.eyJmb28iOiJiYXIifQ.{sig_b64}",
            kid=sig.kid,
        )
        assert verify_agent_card(card, forged, pub) is False

    def test_bad_alg_in_header_rejected(self) -> None:
        """A 'none' alg attack must fail even if the rest of the JWS looks ok."""
        priv, pub = generate_ed25519_keypair()
        card = _sample_card()
        sig = sign_agent_card(card, priv)
        _header_b64, _empty, sig_b64 = sig.detached_jws.split(".")

        from base64 import urlsafe_b64encode

        bad_header = urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "agent-card+jws"}).encode()
        ).rstrip(b"=").decode("ascii")
        forged = AgentCardSignature(
            detached_jws=f"{bad_header}..{sig_b64}",
            kid=sig.kid,
        )
        assert verify_agent_card(card, forged, pub) is False
