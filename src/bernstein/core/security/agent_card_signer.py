"""Sign and verify ``AgentIdentityCard`` instances for A2A v1.0 federation.

The current ``AgentIdentityCard.card_hash`` is a SHA-256 over the JSON body —
useful as an internal HMAC anchor but not a *signature*. Third-party A2A
verifiers won't accept a hash, so any Bernstein agent that wants to federate
with another A2A-speaking system has to fall back to bespoke trust.

This module wraps the existing card body in a detached **JSON Web Signature**
(RFC 7515 compact form) over the JCS-canonicalized (RFC 8785) bytes, signed
with **Ed25519** (RFC 8037 / EdDSA). The card body is left untouched, so the
existing ``card_hash`` stays stable through the transition — verifiers that
understand A2A v1.0 read the JWS, while internal code keeps using the body.

Tracks `kcolbchain/switchboard#25`-style A2A spec work and bernstein
`#1095`. Future work (deferred to a follow-up PR):

- ``/.well-known/agent.json`` HTTP route.
- JWKS endpoint at ``/.well-known/agent.json/keys``.
- Adding A2A v1.0 fields (``protocol_version``, ``supported_interfaces``,
  ``security_schemes``, ``signatures``) to ``AgentIdentityCard`` itself.
- RFC 8707 Resource Indicators in ``auth_middleware``.

The Ed25519 primitives reuse the same ``cryptography`` package already used
by ``sigstore_attestation`` and the HIPAA AES-GCM helpers.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent_identity import AgentIdentityCard

__all__ = [
    "AgentCardSignature",
    "canonicalize_jcs",
    "generate_ed25519_keypair",
    "sign_agent_card",
    "verify_agent_card",
]


# ---------------------------------------------------------------------------
# JCS (RFC 8785) canonicalization
# ---------------------------------------------------------------------------


def canonicalize_jcs(value: Any) -> bytes:
    """Return the RFC 8785 canonical JSON encoding of ``value`` as UTF-8 bytes.

    Implements the spec's deterministic encoding rules sufficient for the
    AgentIdentityCard surface (strings, ints, floats from ``time.time()``,
    booleans, lists, and dicts):

    - Object keys sorted lexicographically by code-point.
    - No insignificant whitespace; ``,`` and ``:`` separators only.
    - Strings emitted with UTF-8, escaping ``"``, ``\\``, and control chars.
    - Numbers via Python's ``json.dumps`` (which matches IEEE 754 double
      shortest round-trip for the typed values we use).

    Note:
        For card bodies that include arbitrary numeric edge cases (NaN,
        ±Infinity, integers past 2**53), upgrade the number serializer
        per RFC 8785 §3.2.2.3 before adopting this for those payloads.
        The cards we sign do not produce those values today.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _b64url(data: bytes) -> str:
    """Base64-url-encode without padding (RFC 7515 §2)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """Base64-url-decode, restoring padding."""
    pad = -len(data) % 4
    return base64.urlsafe_b64decode(data + ("=" * pad))


# ---------------------------------------------------------------------------
# Ed25519 keypair management
# ---------------------------------------------------------------------------


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh Ed25519 keypair.

    Returns:
        ``(private_key_pkcs8_pem, public_key_spki_pem)``

    The PEM-encoded private key is in PKCS#8 (the format read by
    :class:`cryptography.hazmat.primitives.serialization.load_pem_private_key`)
    and the public key is in SubjectPublicKeyInfo. Both formats round-trip
    cleanly through ``cryptography`` and through standard JOSE libraries.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# JWS detached signature
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCardSignature:
    """A JWS-detached signature over a card's JCS canonicalization.

    The card body is NOT inlined in the signature object — third-party
    verifiers receive the canonical card body alongside the JWS and recompute
    the same signing input. This matches RFC 7515 §A.5 (detached content)
    and avoids drift between the body the system trusts internally and the
    bytes the JWS attests to.
    """

    #: Compact-JWS string ``base64url(header).base64url(payload).base64url(sig)``
    #: where ``payload`` is empty (detached) per RFC 7515 §A.5. Verifiers
    #: must reconstruct the canonical card bytes from the body they see.
    detached_jws: str

    #: Key identifier — opaque to the protocol; a stable identifier such as
    #: ``"agent-{agent_id}"`` or a thumbprint hex.
    kid: str

    #: Algorithm name from RFC 7518 §3.1; always ``"EdDSA"`` here.
    alg: str = "EdDSA"


def sign_agent_card(
    card: AgentIdentityCard,
    private_key_pem: bytes,
    *,
    kid: str | None = None,
) -> AgentCardSignature:
    """Sign a card body with the given Ed25519 PKCS#8 PEM private key.

    Args:
        card: The card to sign. Untouched by this call — the JCS bytes are
            computed from a temporary dict so the caller's instance keeps
            its existing ``card_hash`` semantics.
        private_key_pem: PEM-encoded PKCS#8 Ed25519 private key, as produced
            by :func:`generate_ed25519_keypair`.
        kid: Optional key identifier. Defaults to ``"agent-{agent_id}"``.

    Returns:
        An :class:`AgentCardSignature` whose ``detached_jws`` carries an
        empty payload segment (RFC 7515 §A.5).
    """
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(private_key_pem, password=None)

    # Build the JWS protected header (RFC 7515 §4) then base64url it.
    header = {"alg": "EdDSA", "typ": "agent-card+jws", "kid": kid or f"agent-{card.agent_id}"}
    header_b64 = _b64url(canonicalize_jcs(header))

    # JCS-canonicalize the card body. We do NOT include the body in the JWS
    # payload segment (detached signature) but we do sign over its bytes.
    body_b64 = _b64url(canonicalize_jcs(_card_to_dict(card)))

    signing_input = f"{header_b64}.{body_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    sig_b64 = _b64url(signature)

    # RFC 7515 §A.5: detached content omits the payload — represented as the
    # empty string between the header and signature dots.
    detached = f"{header_b64}..{sig_b64}"
    return AgentCardSignature(detached_jws=detached, kid=header["kid"])


def verify_agent_card(
    card: AgentIdentityCard,
    signature: AgentCardSignature,
    public_key_pem: bytes,
) -> bool:
    """Verify a detached JWS over a card body. Returns True iff valid."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    try:
        header_b64, payload_b64, sig_b64 = signature.detached_jws.split(".")
    except ValueError:
        return False

    if payload_b64:
        # Not a detached signature — refuse rather than silently accept.
        return False

    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return False

    if header.get("alg") != "EdDSA":
        return False

    public_key = serialization.load_pem_public_key(public_key_pem)

    body_b64 = _b64url(canonicalize_jcs(_card_to_dict(card)))
    signing_input = f"{header_b64}.{body_b64}".encode("ascii")
    try:
        sig = _b64url_decode(sig_b64)
    except ValueError:
        return False

    try:
        public_key.verify(sig, signing_input)
    except InvalidSignature:
        return False
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _card_to_dict(card: AgentIdentityCard) -> dict[str, Any]:
    """Return the card body as a plain dict suitable for JCS canonicalization.

    Mirrors ``AgentIdentityCard.to_json``'s ``asdict`` result so signing input
    and the body shipped to verifiers agree byte-for-byte after canonicalization.
    """
    from dataclasses import asdict

    return asdict(card)
