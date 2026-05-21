"""Customer-key signing layer for lineage records (schema v2).

Bernstein already chains every WAL entry with an HMAC, so the
orchestrator can self-verify a run hasn't been tampered with. That
chain is signed by Bernstein, not the customer -- a sovereign auditor
who refuses to trust upstream signing keys can't validate it.

This module adds a second, independent signature that the *customer*
controls. A :class:`LineageSigner` is plugged into
:class:`bernstein.core.persistence.lineage.LineageWriter`; every record
emitted while the signer is set carries a detached signature over the
canonicalised record bytes (see ``canonical_record_bytes``). The
signature is base64-encoded into ``LineageRecord.customer_signature``
so it round-trips through the WAL without binary escaping.

Default implementation
----------------------
:class:`Ed25519FileKeySigner` reads a customer-provided Ed25519 private
key from disk in either PEM or raw 32-byte form. We pick Ed25519 by
default because (a) signatures are 64 bytes - small enough to embed in
every WAL line, (b) signing latency is ~50µs on commodity hardware,
(c) the key format is unambiguous, (d) ``cryptography`` already ships
in Bernstein's dependency closure.

Pluggable backends
------------------
The :class:`LineageSigner` protocol is intentionally narrow: a
``sign(bytes) -> bytes`` call. HSM, TPM, or KMS-backed signers
implement the same protocol - the writer doesn't care where the key
material lives, only that the call returns a signature over the
provided canonical bytes. Verifiers are similarly pluggable via
:class:`LineageVerifier`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@runtime_checkable
class LineageSigner(Protocol):
    """Anything that can sign canonicalised lineage record bytes."""

    def sign(self, payload: bytes) -> bytes:
        """Return a detached signature over *payload*."""
        ...


@runtime_checkable
class LineageVerifier(Protocol):
    """Anything that can verify a detached signature."""

    def verify(self, payload: bytes, signature: bytes) -> bool:
        """Return ``True`` iff *signature* is valid for *payload*."""
        ...


class LineageSignerError(RuntimeError):
    """Raised for unrecoverable signer setup or operation errors."""


class Ed25519FileKeySigner:
    """Ed25519 signer that reads a customer's private key from disk.

    Accepts either a PEM-encoded ``PRIVATE KEY`` block (PKCS#8) or a
    raw 32-byte seed. Pick PEM for human-managed keys, raw for keys
    materialised from a KMS/HSM export. The key is loaded eagerly at
    construction so a missing/corrupt key fails fast instead of at
    first emit.
    """

    __slots__ = ("_private_key", "key_path")

    def __init__(self, key_path: Path, private_key: Ed25519PrivateKey) -> None:
        self.key_path = key_path
        self._private_key = private_key

    @classmethod
    def from_path(cls, key_path: Path) -> Ed25519FileKeySigner:
        if not key_path.exists():
            raise LineageSignerError(f"signing key not found: {key_path}")
        try:
            data = key_path.read_bytes()
        except OSError as exc:
            raise LineageSignerError(f"cannot read signing key {key_path}: {exc}") from exc
        return cls(key_path, _load_ed25519_private(data, key_path))

    def sign(self, payload: bytes) -> bytes:
        return self._private_key.sign(payload)

    def public_key_bytes(self) -> bytes:
        """Return the raw 32-byte public key (for handing to the auditor)."""
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )


class Ed25519PublicKeyVerifier:
    """Verifier paired with :class:`Ed25519FileKeySigner`.

    Constructed from either a raw 32-byte public key (e.g. material
    handed to the customer auditor out-of-band) or a PEM-encoded
    public key on disk.
    """

    __slots__ = ("_public_key",)

    def __init__(self, public_key: Ed25519PublicKey) -> None:
        self._public_key = public_key

    @classmethod
    def from_raw(cls, raw: bytes) -> Ed25519PublicKeyVerifier:
        if len(raw) != 32:
            raise LineageSignerError(f"raw Ed25519 public key must be 32 bytes, got {len(raw)}")
        return cls(Ed25519PublicKey.from_public_bytes(raw))

    @classmethod
    def from_path(cls, key_path: Path) -> Ed25519PublicKeyVerifier:
        if not key_path.exists():
            raise LineageSignerError(f"public key not found: {key_path}")
        data = key_path.read_bytes()
        if data.lstrip().startswith(b"-----BEGIN"):
            try:
                public_key = serialization.load_pem_public_key(data)
            except ValueError as exc:
                raise LineageSignerError(f"invalid PEM public key {key_path}: {exc}") from exc
            if not isinstance(public_key, Ed25519PublicKey):
                raise LineageSignerError(f"public key {key_path} is not Ed25519")
            return cls(public_key)
        return cls.from_raw(data.strip() if len(data) > 32 else data)

    def verify(self, payload: bytes, signature: bytes) -> bool:
        try:
            self._public_key.verify(signature, payload)
        except InvalidSignature:
            return False
        return True


def _load_ed25519_private(data: bytes, source: Path) -> Ed25519PrivateKey:
    if data.lstrip().startswith(b"-----BEGIN"):
        try:
            private_key = serialization.load_pem_private_key(data, password=None)
        except (ValueError, TypeError) as exc:
            raise LineageSignerError(f"invalid PEM private key {source}: {exc}") from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise LineageSignerError(f"private key {source} is not Ed25519")
        return private_key
    raw = data.strip() if len(data) > 32 else data
    if len(raw) != 32:
        raise LineageSignerError(
            f"raw Ed25519 private key must be 32 bytes (got {len(raw)} from {source})",
        )
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise LineageSignerError(f"cannot load raw Ed25519 key from {source}: {exc}") from exc


# ---------------------------------------------------------------------------
# Attachment-as-parent helper (issue #1797)
# ---------------------------------------------------------------------------
# Additive, append-only surface: the lineage receipt for any artefact
# produced by a worker this turn must carry the input attachment's
# SHA-256 in its parents list. The helper below builds the canonical
# parent identifier from an attachment digest so that callers do not
# have to know the URI format.

_ATTACHMENT_PARENT_SCHEME = "multimodal-attachment://"
_HEX_DIGIT_SET = frozenset("0123456789abcdef")


def build_attachment_parent_uri(sha256: str) -> str:
    """Return the canonical parent URI for a multimodal attachment.

    Args:
        sha256: Hex digest of the attachment bytes (lower-case, 64 chars).

    Returns:
        A scheme-qualified content-addressed URI suitable for inclusion
        in a lineage record's ``parents`` list.

    Raises:
        LineageSignerError: When *sha256* is not exactly 64 lower-case
            hexadecimal characters. (bot-ack: 3284182781 --
            CodeRabbit major.)
    """
    if not sha256 or len(sha256) != 64:
        raise LineageSignerError(f"attachment sha256 must be 64 hex chars, got {len(sha256)}")
    if not _HEX_DIGIT_SET.issuperset(sha256):
        raise LineageSignerError("attachment sha256 must be lower-case hex (0-9a-f)")
    return f"{_ATTACHMENT_PARENT_SCHEME}{sha256}"


def register_attachment_parents(
    parents: list[str],
    attachment_sha256s: list[str],
) -> list[str]:
    """Append attachment parent URIs to an existing lineage parents list.

    The function never mutates *parents*; it returns a new list with
    the existing parents followed by the attachment-derived URIs.
    Duplicate entries are filtered out so a multiply-attached image
    appears exactly once in the receipt.

    Args:
        parents: The existing lineage parents list.
        attachment_sha256s: Hex digests of attachments to register.

    Returns:
        A new list with attachment parents appended.
    """
    seen: set[str] = set(parents)
    out: list[str] = parents.copy()
    for digest in attachment_sha256s:
        uri = build_attachment_parent_uri(digest)
        if uri not in seen:
            out.append(uri)
            seen.add(uri)
    return out


def signer_from_config(
    *,
    enabled: bool,
    key_path: str | None = None,
    key_kind: str = "ed25519",
    kms_adapter: str | None = None,
    kms_env_var: str | None = None,
    kms_token_uri: str | None = None,
    kms_kid: str | None = None,
) -> LineageSigner | None:
    """Build a :class:`LineageSigner` from bernstein.yaml-shaped config.

    Two configuration shapes are supported:

    * **Phase-1 file-only** (back-compat): ``enabled=True`` +
      ``key_path=...`` reads an Ed25519 PEM/raw key off disk. Equivalent
      to ``kms_adapter='file'`` + ``key_path=...``.
    * **Phase-2 KMS-pluggable**: ``kms_adapter='file'|'env'|'hsm'``
      dispatches to the matching ``KMSAdapter`` from
      :mod:`bernstein.core.security.lineage_kms`. The HSM adapter is a
      documented stub (raises ``NotImplementedError``) so the wiring is
      in place even when the customer integration isn't.

    Returns ``None`` when signing is disabled or unconfigured. Raises
    :class:`LineageSignerError` when ``enabled=True`` but the key cannot
    be loaded - the orchestrator should fail fast rather than silently
    drop signatures.
    """
    if not enabled:
        return None
    # Phase-2 path: explicit kms_adapter selector.
    if kms_adapter is not None:
        # Imported lazily so this module stays free of the security
        # package import cycle (lineage_kms imports back from here).
        from bernstein.core.security.lineage_kms import kms_adapter_from_config

        return kms_adapter_from_config(
            enabled=True,
            kind=kms_adapter,
            key_path=key_path,
            env_var=kms_env_var,
            token_uri=kms_token_uri,
            kid=kms_kid,
        )
    # Phase-1 path: file key by default.
    if key_path is None:
        raise LineageSignerError("lineage.customer_signing.enabled=true requires key_path")
    if key_kind != "ed25519":
        raise LineageSignerError(
            f"unsupported lineage.customer_signing.key_kind: {key_kind!r} (only 'ed25519' is implemented in Phase 1)",
        )
    return Ed25519FileKeySigner.from_path(Path(key_path))
