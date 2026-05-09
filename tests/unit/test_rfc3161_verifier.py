"""Tests for the RFC 3161 TimeStampToken verifier (resrch-005 v2).

Exercises the verifier against the real FreeTSA fixture checked into
``tests/fixtures/rfc3161/``. Covers:

* Valid chain — token + payload + trust bundle → ``ok=True``.
* Tampered token — flipped bytes → signature failure surfaces.
* Wrong payload — messageImprint mismatch flagged.
* Missing trust bundle — empty list rejected with explicit error.
* TSA EKU surface — ``id-kp-timeStamping`` is detected.
* Trust bundle loader — PEM and DER paths parsed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bernstein.core.security.rfc3161_verifier import (
    RFC3161Verification,
    hash_payload_for_tsa,
    load_trusted_tsa_certs,
    verify_rfc3161_token,
)

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "rfc3161"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def freetsa_token() -> bytes:
    return (_FIXTURE_DIR / "freetsa_token_with_certs.tsr").read_bytes()


@pytest.fixture(scope="module")
def freetsa_payload() -> bytes:
    return (_FIXTURE_DIR / "freetsa_payload.txt").read_bytes()


@pytest.fixture(scope="module")
def freetsa_trust() -> list:
    return load_trusted_tsa_certs(_FIXTURE_DIR / "freetsa_cacert.pem")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidChain:
    """A genuine FreeTSA token verifies end-to-end."""

    def test_valid_freetsa_token_passes(
        self,
        freetsa_token: bytes,
        freetsa_payload: bytes,
        freetsa_trust: list,
    ) -> None:
        payload_hash = hash_payload_for_tsa(freetsa_payload, algorithm="sha256")
        result = verify_rfc3161_token(freetsa_token, payload_hash, freetsa_trust)
        assert result.ok, result.errors
        assert result.gen_time is not None
        assert result.tsa_subject is not None
        assert "Free TSA" in result.tsa_subject
        assert result.hash_algorithm == "sha256"
        assert result.eku_timestamping is True
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


class TestInvalidChain:
    """The verifier surfaces every observable failure mode."""

    def test_payload_hash_mismatch_fails(
        self,
        freetsa_token: bytes,
        freetsa_trust: list,
    ) -> None:
        result = verify_rfc3161_token(
            freetsa_token,
            payload_hash=b"\x00" * 32,
            trusted_tsa_certs=freetsa_trust,
        )
        assert not result.ok
        assert any("messageImprint mismatch" in e for e in result.errors)

    def test_empty_trust_bundle_fails(
        self,
        freetsa_token: bytes,
        freetsa_payload: bytes,
    ) -> None:
        result = verify_rfc3161_token(
            freetsa_token,
            payload_hash=hash_payload_for_tsa(freetsa_payload),
            trusted_tsa_certs=[],
        )
        assert not result.ok
        assert any("trusted TSA cert list is empty" in e for e in result.errors)

    def test_garbage_token_fails_to_parse(
        self,
        freetsa_payload: bytes,
        freetsa_trust: list,
    ) -> None:
        result = verify_rfc3161_token(
            b"not a real token at all",
            payload_hash=hash_payload_for_tsa(freetsa_payload),
            trusted_tsa_certs=freetsa_trust,
        )
        assert not result.ok
        assert any("parse:" in e for e in result.errors)

    def test_tampered_token_signature_fails(
        self,
        freetsa_token: bytes,
        freetsa_payload: bytes,
        freetsa_trust: list,
    ) -> None:
        bad = bytearray(freetsa_token)
        # Mutate a byte deep inside the SignerInfo signature region (after
        # the embedded TSTInfo, where the CMS signature lives).
        bad[len(bad) - 50] ^= 0x42
        result = verify_rfc3161_token(
            bytes(bad),
            payload_hash=hash_payload_for_tsa(freetsa_payload),
            trusted_tsa_certs=freetsa_trust,
        )
        assert not result.ok

    def test_tampered_tst_info_fails(
        self,
        freetsa_token: bytes,
        freetsa_payload: bytes,
        freetsa_trust: list,
    ) -> None:
        """Flipping a byte inside TSTInfo flips the message-digest check."""
        bad = bytearray(freetsa_token)
        # Find the embedded payload hash and flip its first byte.
        payload_hash = hash_payload_for_tsa(freetsa_payload, algorithm="sha256")
        idx = bytes(bad).find(payload_hash)
        assert idx > 0, "expected payload hash inside TSTInfo"
        bad[idx] ^= 0x01
        result = verify_rfc3161_token(
            bytes(bad),
            payload_hash=payload_hash,
            trusted_tsa_certs=freetsa_trust,
        )
        assert not result.ok


# ---------------------------------------------------------------------------
# Trust bundle loader
# ---------------------------------------------------------------------------


class TestTrustBundleLoader:
    """``load_trusted_tsa_certs`` accepts PEM + DER, rejects empty/missing."""

    def test_pem_bundle_loads(self, freetsa_trust: list) -> None:
        assert len(freetsa_trust) >= 1
        # FreeTSA's cacert.pem ships a single root.
        assert freetsa_trust[0].subject.rfc4514_string()

    def test_concatenated_pem_loads(self, tmp_path: Path) -> None:
        cacert = (_FIXTURE_DIR / "freetsa_cacert.pem").read_bytes()
        tsa_crt = (_FIXTURE_DIR / "freetsa_tsa.crt").read_bytes()
        bundle = tmp_path / "bundle.pem"
        bundle.write_bytes(cacert + b"\n" + tsa_crt)
        certs = load_trusted_tsa_certs(bundle)
        assert len(certs) == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            load_trusted_tsa_certs(tmp_path / "nope.pem")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.pem"
        empty.write_bytes(b"")
        with pytest.raises(ValueError, match="empty"):
            load_trusted_tsa_certs(empty)


# ---------------------------------------------------------------------------
# Helper invariants
# ---------------------------------------------------------------------------


class TestHashPayloadForTSA:
    """Helper rejects unsupported algorithms."""

    def test_sha256_default(self) -> None:
        h = hash_payload_for_tsa(b"hello")
        assert len(h) == 32

    def test_sha384_supported(self) -> None:
        assert len(hash_payload_for_tsa(b"hello", algorithm="sha384")) == 48

    def test_sha1_rejected(self) -> None:
        with pytest.raises(ValueError, match="sha1"):
            hash_payload_for_tsa(b"hello", algorithm="sha1")

    def test_unknown_rejected(self) -> None:
        with pytest.raises(ValueError, match="md5"):
            hash_payload_for_tsa(b"hello", algorithm="md5")


# ---------------------------------------------------------------------------
# Verification result type
# ---------------------------------------------------------------------------


class TestVerificationResult:
    """The dataclass surfaces enough metadata for downstream policy checks."""

    def test_result_is_frozen(self) -> None:
        result = RFC3161Verification(ok=True)
        with pytest.raises(Exception, match="cannot assign"):
            result.ok = False  # type: ignore[misc]
