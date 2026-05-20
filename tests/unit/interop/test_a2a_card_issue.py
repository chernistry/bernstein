"""Tests for issuing and signing A2A capability cards (AC 1)."""

from __future__ import annotations

import json

import pytest

from bernstein.core.interop.a2a_card import (
    CAPABILITY_CARD_TYP,
    CapabilityCard,
    CardPolicies,
    SignedCapabilityCard,
    card_public_key_fingerprint,
    issue_capability_card,
    verify_capability_card,
)


def _policies() -> CardPolicies:
    return CardPolicies(cost_cap_usd=10.0, redaction_tier="standard", sandbox_profile="container")


def test_issue_card_carries_required_fields() -> None:
    signed, private_key = issue_capability_card(
        issuer="acme",
        name="Acme Orchestrator",
        description="does things",
        advertised_tools=["task_orchestration", "code_review"],
        policies=_policies(),
        ttl_seconds=3600,
        now=1_000.0,
    )
    card = signed.card
    assert card.issuer == "acme"
    assert card.name == "Acme Orchestrator"
    assert card.advertised_tools == ["task_orchestration", "code_review"]
    assert card.policies.cost_cap_usd == 10.0
    assert card.policies.redaction_tier == "standard"
    assert card.policies.sandbox_profile == "container"
    # public key for verification is carried, expiry is set.
    assert "BEGIN PUBLIC KEY" in card.public_key_pem
    assert card.created_at == 1_000.0
    assert card.expires_at == 1_000.0 + 3600
    # the private key is returned so the operator can re-issue.
    assert b"BEGIN PRIVATE KEY" in private_key


def test_issued_card_verifies() -> None:
    signed, _ = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d",
        advertised_tools=["t"],
        policies=_policies(),
        ttl_seconds=3600,
    )
    assert verify_capability_card(signed) is True


def test_signature_uses_capability_card_typ() -> None:
    signed, _ = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d",
        advertised_tools=["t"],
        policies=_policies(),
    )
    header_b64 = signed.signature.split(".")[0]
    import base64

    pad = -len(header_b64) % 4
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * pad))
    assert header["typ"] == CAPABILITY_CARD_TYP
    assert header["alg"] == "EdDSA"


def test_tampered_body_fails_verification() -> None:
    import dataclasses

    signed, _ = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d",
        advertised_tools=["t"],
        policies=_policies(),
        ttl_seconds=3600,
    )
    tampered = dataclasses.replace(signed, card=dataclasses.replace(signed.card, issuer="evil"))
    assert verify_capability_card(tampered) is False


def test_expired_card_rejected_by_default() -> None:
    signed, _ = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d",
        advertised_tools=["t"],
        policies=_policies(),
        ttl_seconds=3600,
        now=1_000.0,
    )
    # now well past expiry.
    assert verify_capability_card(signed, now=1_000.0 + 99_999) is False
    # signature itself stays valid when expiry is not checked.
    assert verify_capability_card(signed, check_expiry=False, now=1_000.0 + 99_999) is True


def test_card_json_round_trips() -> None:
    signed, _ = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d",
        advertised_tools=["a", "b"],
        policies=_policies(),
        ttl_seconds=3600,
    )
    text = signed.to_json()
    reloaded = SignedCapabilityCard.from_json(text)
    assert reloaded.card.to_body() == signed.card.to_body()
    assert reloaded.signature == signed.signature
    # round-trip preserves cryptographic validity.
    assert verify_capability_card(reloaded) is True


def test_fingerprint_is_stable_for_same_key() -> None:
    signed, private_key = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d",
        advertised_tools=["t"],
        policies=_policies(),
    )
    fp1 = card_public_key_fingerprint(signed.card.public_key_pem)
    # re-issuing with the same private key keeps the same fingerprint.
    signed2, _ = issue_capability_card(
        issuer="acme",
        name="Acme",
        description="d2",
        advertised_tools=["t"],
        policies=_policies(),
        private_key_pem=private_key,
    )
    fp2 = card_public_key_fingerprint(signed2.card.public_key_pem)
    assert fp1 == fp2 == fp1
    assert fp1.startswith("sha256:")


def test_fresh_key_per_issue_when_unspecified() -> None:
    s1, _ = issue_capability_card(issuer="a", name="a", description="d", advertised_tools=[], policies=_policies())
    s2, _ = issue_capability_card(issuer="a", name="a", description="d", advertised_tools=[], policies=_policies())
    assert card_public_key_fingerprint(s1.card.public_key_pem) != card_public_key_fingerprint(s2.card.public_key_pem)


@pytest.mark.parametrize(
    "bad_body",
    [
        {"schema_version": ""},
        {"advertised_tools": "not-a-list"},
        {"policies": {"cost_cap_usd": -1, "redaction_tier": "x", "sandbox_profile": "y"}},
    ],
)
def test_invalid_body_rejected(bad_body: dict[str, object]) -> None:
    base = {
        "schema_version": "1",
        "issuer": "acme",
        "name": "Acme",
        "description": "d",
        "advertised_tools": ["t"],
        "policies": {"cost_cap_usd": 1.0, "redaction_tier": "standard", "sandbox_profile": "container"},
        "public_key_pem": "x",
        "kid": "k",
        "created_at": 1.0,
        "expires_at": 2.0,
    }
    base.update(bad_body)
    with pytest.raises(ValueError):
        CapabilityCard.from_body(base)
