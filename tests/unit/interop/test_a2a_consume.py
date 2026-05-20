"""Tests for the consume side: verify + trust + policy gate (AC 2)."""

from __future__ import annotations

import dataclasses

import pytest

from bernstein.core.interop.a2a_card import (
    CardPolicies,
    card_public_key_fingerprint,
    issue_capability_card,
)
from bernstein.core.interop.a2a_consume import (
    PeerCardRejected,
    PolicyRequirements,
    consume_peer_card,
    policies_meet_requirements,
)


def _signed(policies: CardPolicies, *, ttl_seconds: int = 3600):
    return issue_capability_card(
        issuer="peer",
        name="Peer",
        description="d",
        advertised_tools=["t"],
        policies=policies,
        ttl_seconds=ttl_seconds,
    )


def test_policy_gate_accepts_compliant_peer() -> None:
    policies = CardPolicies(cost_cap_usd=5.0, redaction_tier="strict", sandbox_profile="microvm")
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    verdict = policies_meet_requirements(policies, req)
    assert verdict.ok is True
    assert verdict.failures == []


def test_policy_gate_rejects_high_cost_cap() -> None:
    policies = CardPolicies(cost_cap_usd=50.0, redaction_tier="strict", sandbox_profile="microvm")
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    verdict = policies_meet_requirements(policies, req)
    assert verdict.ok is False
    assert any("cost cap" in f for f in verdict.failures)


def test_policy_gate_rejects_weaker_sandbox_and_redaction() -> None:
    policies = CardPolicies(cost_cap_usd=1.0, redaction_tier="basic", sandbox_profile="process")
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="strict", min_sandbox_profile="microvm")
    verdict = policies_meet_requirements(policies, req)
    assert verdict.ok is False
    assert any("redaction" in f for f in verdict.failures)
    assert any("sandbox" in f for f in verdict.failures)


def test_unknown_ordinal_fails_closed() -> None:
    policies = CardPolicies(cost_cap_usd=1.0, redaction_tier="bespoke", sandbox_profile="container")
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    verdict = policies_meet_requirements(policies, req)
    assert verdict.ok is False
    assert any("not a known tier" in f for f in verdict.failures)


def test_consume_accepts_trusted_compliant_peer() -> None:
    policies = CardPolicies(cost_cap_usd=5.0, redaction_tier="strict", sandbox_profile="microvm")
    signed, _ = _signed(policies)
    fp = card_public_key_fingerprint(signed.card.public_key_pem)
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    verdict = consume_peer_card(signed, trusted_issuer_fingerprints=[fp], requirements=req)
    assert verdict.ok is True


def test_consume_rejects_untrusted_issuer() -> None:
    policies = CardPolicies(cost_cap_usd=5.0, redaction_tier="strict", sandbox_profile="microvm")
    signed, _ = _signed(policies)
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    with pytest.raises(PeerCardRejected) as exc:
        consume_peer_card(signed, trusted_issuer_fingerprints=["sha256:not-this-one"], requirements=req)
    assert exc.value.reason == "untrusted_issuer"


def test_consume_rejects_bad_signature() -> None:
    policies = CardPolicies(cost_cap_usd=5.0, redaction_tier="strict", sandbox_profile="microvm")
    signed, _ = _signed(policies)
    fp = card_public_key_fingerprint(signed.card.public_key_pem)
    tampered = dataclasses.replace(signed, card=dataclasses.replace(signed.card, issuer="evil"))
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    with pytest.raises(PeerCardRejected) as exc:
        consume_peer_card(tampered, trusted_issuer_fingerprints=[fp], requirements=req)
    assert exc.value.reason == "signature"


def test_consume_rejects_policy_violation() -> None:
    policies = CardPolicies(cost_cap_usd=50.0, redaction_tier="basic", sandbox_profile="process")
    signed, _ = _signed(policies)
    fp = card_public_key_fingerprint(signed.card.public_key_pem)
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="strict", min_sandbox_profile="microvm")
    with pytest.raises(PeerCardRejected) as exc:
        consume_peer_card(signed, trusted_issuer_fingerprints=[fp], requirements=req)
    assert exc.value.reason == "policy"


def test_consume_rejects_expired_card() -> None:
    policies = CardPolicies(cost_cap_usd=1.0, redaction_tier="strict", sandbox_profile="microvm")
    signed, _ = issue_capability_card(
        issuer="peer",
        name="Peer",
        description="d",
        advertised_tools=["t"],
        policies=policies,
        ttl_seconds=3600,
        now=1_000.0,
    )
    fp = card_public_key_fingerprint(signed.card.public_key_pem)
    req = PolicyRequirements(max_cost_cap_usd=10.0, min_redaction_tier="standard", min_sandbox_profile="container")
    with pytest.raises(PeerCardRejected) as exc:
        consume_peer_card(signed, trusted_issuer_fingerprints=[fp], requirements=req, now=1_000.0 + 99_999)
    assert exc.value.reason == "signature"
