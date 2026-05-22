"""Agent identity + Ed25519 JWS detached signing.

The lineage layer signs every entry with an Ed25519 keypair issued per agent
invocation. The Agent Card subset modelled here is the slice of the A2A v1.0
Agent Card spec that's actually load-bearing for lineage verification - the
agent id, the key id, and the PEM-encoded public key. External tools (auditor
CLI) hold only the public side and a copy of the card; the operator-side
recorder holds the private key.

Detached JWS follows RFC 7515 Appendix F + the `b64=false` unencoded-payload
option (RFC 7797). Algorithm is EdDSA per RFC 8037.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass(frozen=True, slots=True)
class AgentCard:
    """Subset of the A2A v1.0 Agent Card relevant to lineage signing."""

    agent_id: str
    kid: str
    public_key_pem: str
    protocol_version: str = "a2a/1.0"


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 keypair. Returns (private_pem, public_pem)."""
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    pub_pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return priv_pem, pub_pem


def sign_detached(payload: bytes, private_key_pem: str, *, kid: str) -> str:
    """Produce an Ed25519 JWS in detached form (RFC 7515 + RFC 7797).

    The compact serialisation is `<protected>..<signature>` - the middle
    segment (payload) is empty because the verifier supplies the canonical
    bytes out-of-band. This keeps the on-disk `.jws` file independent of
    the entry it covers and lets the auditor re-canonicalise locally.
    """
    priv = serialization.load_pem_private_key(private_key_pem.encode("ascii"), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise TypeError("sign_detached requires an Ed25519 private key")
    header = {"alg": "EdDSA", "kid": kid, "b64": False, "crit": ["b64"]}
    protected = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = protected.encode("ascii") + b"." + payload
    sig = priv.sign(signing_input)
    return protected + ".." + _b64url(sig)


def jws_header_kid(jws: str) -> str | None:
    """Return the ``kid`` from a detached JWS protected header.

    Returns ``None`` when the JWS is malformed or the header has no string
    ``kid``. The gate uses this to bind the *signed-body* ``agent_card_kid``
    to the JWS the entry actually carries (issue #1837); a divergence between
    the two is a verification failure, not merely a wrong key. Never raises on
    bad input.
    """
    try:
        protected_b64, empty, sig_b64 = jws.split(".", maxsplit=2)
    except ValueError:
        return None
    if empty != "" or "." in sig_b64:
        return None
    try:
        header = json.loads(_b64url_decode(protected_b64))
    except (ValueError, json.JSONDecodeError, binascii.Error, TypeError):
        # ``binascii.Error`` is a ``ValueError`` subclass on CPython, so the
        # bare ``ValueError`` above already catches malformed base64url; it is
        # named explicitly to keep the "Never raises" contract robust if that
        # hierarchy ever changes. ``TypeError`` guards a non-``str`` header
        # segment reaching the decoder.
        return None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


def verify_detached(payload: bytes, jws: str, card: AgentCard) -> bool:
    """Verify a detached Ed25519 JWS against the Agent Card's public key.

    Returns True on cryptographic success and matching kid; False on any
    malformed input, mismatched kid, wrong key, or invalid signature.
    Never raises on bad input.
    """
    try:
        protected_b64, empty, sig_b64 = jws.split(".", maxsplit=2)
    except ValueError:
        return False
    if empty != "":
        return False
    if "." in sig_b64:
        return False  # 4+ segments
    try:
        header = json.loads(_b64url_decode(protected_b64))
    except (ValueError, json.JSONDecodeError, binascii.Error, TypeError):
        return False
    if header.get("alg") != "EdDSA":
        return False
    if header.get("kid") != card.kid:
        return False
    try:
        pub = serialization.load_pem_public_key(card.public_key_pem.encode("ascii"))
    except (ValueError, TypeError):
        return False
    if not isinstance(pub, Ed25519PublicKey):
        return False
    signing_input = protected_b64.encode("ascii") + b"." + payload
    try:
        sig_bytes = _b64url_decode(sig_b64)
    except (ValueError, binascii.Error):
        return False
    try:
        pub.verify(sig_bytes, signing_input)
    except InvalidSignature:
        return False
    return True
