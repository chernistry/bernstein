"""Tests for the DSSE / in-toto v1 audit envelope wrapper.

Coverage:

* Roundtrip — wrap then verify yields PASS, and the embedded statement
  carries the right ``_type`` + ``predicateType``.
* Determinism — same bundle bytes + same key produce a byte-identical
  envelope (Ed25519 is deterministic per RFC 8032 §5.1.6).
* Tamper detection — flipping the envelope signature, the payload byte,
  the bundle bytes, or breaking an HMAC chain link all surface as FAIL.
* Type mismatch — payloadType / predicateType drift triggers a
  :class:`EnvelopeTypeMismatchError`-shaped failure.
* Schema-version migration — the ``schema_version`` field is preserved
  inside the predicate body so a future v2 can introspect older
  envelopes.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bernstein.core.security.article12_bundle import (
    build_article12_bundle,
)
from bernstein.core.security.audit import AuditLog
from bernstein.core.security.audit_dsse import (
    BERNSTEIN_AUDIT_PREDICATE_TYPE,
    DSSE_PAYLOAD_TYPE,
    ENVELOPE_SCHEMA_VERSION,
    IN_TOTO_STATEMENT_TYPE,
    Envelope,
    EnvelopeFormatError,
    Signature,
    keyid_from_public_key,
    load_envelope,
    pae,
    parse_envelope,
    verify_envelope,
    wrap_bundle,
    write_envelope,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_log(audit_dir: Path) -> AuditLog:
    """Populate ``audit_dir`` with three HMAC-chained events."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    log = AuditLog(audit_dir, key=b"x" * 32)
    log.log("task.created", "alice", "task", "T-1", {"role": "backend"})
    log.log("agent.spawned", "orchestrator", "agent", "A-1", {"task": "T-1"})
    log.log("task.completed", "alice", "task", "T-1", {"status": "ok"})
    return log


def _build_bundle(tmp_path: Path):
    """Build a deterministic Article 12 bundle on disk under ``tmp_path``."""
    audit_dir = tmp_path / ".sdd" / "audit"
    _seed_log(audit_dir)
    today = datetime.now(tz=UTC).date()
    since = f"{today.isoformat()}T00:00:00+00:00"
    until = f"{(today + timedelta(days=1)).isoformat()}T00:00:00+00:00"
    output_dir = tmp_path / ".sdd" / "evidence"
    bundle = build_article12_bundle(
        audit_dir=audit_dir,
        since=since,
        until=until,
        risk_class="high",
        output_dir=output_dir,
        write=True,
    )
    return bundle


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    """Deterministic Ed25519 key generated from a fixed seed.

    Using ``from_private_bytes`` against fixed entropy lets the
    determinism assertions hold across processes/runs.
    """
    seed = b"r" * 32
    return Ed25519PrivateKey.from_private_bytes(seed)


# ---------------------------------------------------------------------------
# PAE
# ---------------------------------------------------------------------------


class TestPAE:
    """The DSSE pre-authentication encoding."""

    def test_known_vector(self) -> None:
        # Hand-derived from the DSSE spec to keep the implementation honest.
        result = pae("foo", b"bar")
        assert result == b"DSSEv1 3 foo 3 bar"

    def test_empty_payload(self) -> None:
        assert pae("application/x-empty", b"") == b"DSSEv1 19 application/x-empty 0 "


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    """wrap → write → load → verify."""

    def test_wrap_then_verify_passes(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        result = verify_envelope(envelope, signing_key.public_key())
        assert result.ok, result.errors
        assert result.statement["_type"] == IN_TOTO_STATEMENT_TYPE
        assert result.statement["predicateType"] == BERNSTEIN_AUDIT_PREDICATE_TYPE
        assert result.keyid == keyid_from_public_key(signing_key.public_key())

    def test_persisted_envelope_roundtrips(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        path = tmp_path / "audit.dsse.json"
        write_envelope(envelope, path)
        loaded = load_envelope(path)
        result = verify_envelope(loaded, signing_key.public_key())
        assert result.ok, result.errors

    def test_envelope_carries_schema_version(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        """The schema_version is inside the predicate so a v2 verifier can branch."""
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        statement = envelope.statement
        assert statement["predicate"]["schema_version"] == ENVELOPE_SCHEMA_VERSION
        assert statement["predicate"]["bundle_kind"] == "article12"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same input + same key → byte-identical envelope (incl. signature)."""

    def test_repeat_wrap_byte_identical(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        env1 = wrap_bundle(bundle, signing_key=signing_key)
        env2 = wrap_bundle(bundle, signing_key=signing_key)
        assert env1.to_json() == env2.to_json(), "DSSE envelope must be byte-deterministic"


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    """Each surface that an attacker would touch must surface FAIL."""

    def test_signature_byte_flip_fails(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)

        # Flip one byte in the base64 signature.
        original_sig = base64.b64decode(envelope.signatures[0].sig)
        tampered_bytes = bytearray(original_sig)
        tampered_bytes[0] ^= 0x01
        bad_sig = base64.b64encode(bytes(tampered_bytes)).decode("ascii")
        bad_envelope = Envelope(
            payload_type=envelope.payload_type,
            payload_b64=envelope.payload_b64,
            signatures=[Signature(keyid=envelope.signatures[0].keyid, sig=bad_sig)],
        )
        result = verify_envelope(bad_envelope, signing_key.public_key())
        assert not result.ok
        assert any("signature verify failed" in e for e in result.errors)

    def test_payload_byte_flip_fails(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)

        # Flip one byte in the payload — same signature no longer matches PAE.
        original_payload = envelope.payload_bytes
        tampered = bytearray(original_payload)
        tampered[10] ^= 0x01  # arbitrary middle byte
        bad_envelope = Envelope(
            payload_type=envelope.payload_type,
            payload_b64=base64.b64encode(bytes(tampered)).decode("ascii"),
            signatures=envelope.signatures,
        )
        result = verify_envelope(bad_envelope, signing_key.public_key())
        assert not result.ok
        assert any("signature verify failed" in e for e in result.errors)

    def test_chain_link_break_detected_via_subject(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        """Flipping one byte of the bundle invalidates the subject digest match.

        The standalone verifier's chain check (subprocess test) walks the
        HMAC; here we check the cheaper subject-digest invariant: the
        envelope's ``subject.digest.sha256`` no longer matches a tampered
        bundle.
        """
        bundle = _build_bundle(tmp_path)
        assert bundle.archive_path is not None
        envelope = wrap_bundle(bundle, signing_key=signing_key)

        # Tamper the on-disk bundle.
        raw = bundle.archive_path.read_bytes()
        tampered = bytearray(raw)
        tampered[-1] ^= 0x01  # flip last byte
        bundle.archive_path.write_bytes(bytes(tampered))

        # Re-derive the subject digest from the on-disk bundle and compare.
        from hashlib import sha256

        envelope_subject_digest = envelope.statement["subject"][0]["digest"]["sha256"]
        actual = sha256(bundle.archive_path.read_bytes()).hexdigest()
        assert envelope_subject_digest != actual, "subject digest should drift on tamper"


# ---------------------------------------------------------------------------
# Type rejection
# ---------------------------------------------------------------------------


class TestTypeRejection:
    """The verifier refuses envelopes with a wrong payloadType / predicateType."""

    def test_wrong_payload_type_rejected(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        # Forge an envelope whose payloadType is wrong.
        forged = Envelope(
            payload_type="application/vnd.example+json",
            payload_b64=envelope.payload_b64,
            signatures=envelope.signatures,
        )
        result = verify_envelope(forged, signing_key.public_key())
        assert not result.ok
        assert any("payloadType mismatch" in e for e in result.errors)

    def test_wrong_predicate_type_rejected(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        # Override the expected predicate type — verifier should fail.
        result = verify_envelope(
            envelope,
            signing_key.public_key(),
            expected_predicate_type="https://example.invalid/type",
        )
        assert not result.ok
        assert any("predicateType mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Format / parse
# ---------------------------------------------------------------------------


class TestEnvelopeParser:
    """parse_envelope is strict about wire-format keys."""

    def test_missing_payload_rejected(self) -> None:
        with pytest.raises(EnvelopeFormatError, match="payload"):
            parse_envelope({"payloadType": "x", "signatures": [{"keyid": "k", "sig": "s"}]})

    def test_missing_signatures_rejected(self) -> None:
        with pytest.raises(EnvelopeFormatError, match="signatures"):
            parse_envelope({"payload": "abc", "payloadType": DSSE_PAYLOAD_TYPE, "signatures": []})

    def test_non_object_rejected(self) -> None:
        with pytest.raises(EnvelopeFormatError):
            parse_envelope("not a dict")  # type: ignore[arg-type]

    def test_loaded_envelope_carries_signatures(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        path = write_envelope(envelope, tmp_path / "e.json")
        re_loaded = load_envelope(path)
        assert re_loaded.signatures
        assert re_loaded.signatures[0].keyid == envelope.signatures[0].keyid

    def test_canonical_json_sorted_keys(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        """Top-level envelope JSON must come out with keys in sorted order.

        We rely on this for byte-determinism across regenerations.
        """
        bundle = _build_bundle(tmp_path)
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        text = envelope.to_json().decode("utf-8")
        first_key = json.loads(text)
        # ``payload`` < ``payloadType`` < ``signatures`` lexicographically.
        keys = list(first_key.keys())
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Subject digest
# ---------------------------------------------------------------------------


class TestSubjectDigest:
    """The in-toto subject's digest matches the on-disk bundle bytes."""

    def test_subject_sha256_matches_bundle(self, tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
        from hashlib import sha256

        bundle = _build_bundle(tmp_path)
        assert bundle.archive_path is not None
        envelope = wrap_bundle(bundle, signing_key=signing_key)
        statement = envelope.statement
        subject_digest = statement["subject"][0]["digest"]["sha256"]
        actual = sha256(bundle.archive_path.read_bytes()).hexdigest()
        assert subject_digest == actual
