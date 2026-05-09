"""Tests for the customer-countersignature flow on air-gap wheelhouses.

Covers the two-key chain (org cosign + customer Ed25519) end to end:

* round-trip: org sign -> customer countersign -> verify both pass,
* tampering: modify a wheel after countersign -> verify fails,
* require-customer-sig flag: absence of customer sig fails when set,
* trust store: multiple keys allowed via separate files,
* trust store hygiene: bad keys / non-PEM files are skipped silently,
* metadata sidecar: ``MANIFEST.customer.json`` carries org_name + key.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bernstein.core.distribution.customer_countersign import (
    CUSTOMER_META_FILENAME,
    CUSTOMER_SIG_FILENAME,
    CustomerCountersignError,
    countersign_bundle,
    load_trust_store,
    verify_customer_signature,
)
from bernstein.core.distribution.verifier import verify_wheelhouse

# ---------------------------------------------------------------------------
# Bundle + key fixture helpers
# ---------------------------------------------------------------------------


def _make_pem_keypair(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Generate an Ed25519 keypair on disk; return (priv_path, pub_path)."""
    key = Ed25519PrivateKey.generate()
    priv = tmp_path / f"{name}.pem"
    priv.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    pub = tmp_path / f"{name}.pub.pem"
    pub.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )
    return priv, pub


def _make_bundle(tmp_path: Path, *, wheels: int = 2) -> Path:
    """Materialise a minimal wheelhouse with ``wheels`` fake .whl files + manifest."""
    bundle = tmp_path / "wheelhouse"
    bundle.mkdir()
    entries: list[dict[str, object]] = []
    for idx in range(wheels):
        wheel_name = f"pkg{idx}-1.0-py3-none-any.whl"
        wheel_path = bundle / wheel_name
        wheel_path.write_bytes(f"wheel-content-{idx}".encode())
        sha = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
        entries.append({"name": wheel_name, "sha256": sha, "size": wheel_path.stat().st_size})
    manifest = {
        "version": "1.0.0",
        "generated_at": "2026-05-09T00:00:00+00:00",
        "wheels": sorted(entries, key=lambda e: e["name"]),
    }
    (bundle / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return bundle


def _make_trust_dir(tmp_path: Path, public_keys: list[tuple[str, Path]]) -> Path:
    """Drop ``public_keys`` (named) into a fresh trust directory."""
    trust = tmp_path / "trust"
    trust.mkdir()
    for org_name, pub_path in public_keys:
        (trust / f"{org_name}.pem").write_bytes(pub_path.read_bytes())
    return trust


# ---------------------------------------------------------------------------
# Round-trip: org sign -> customer countersign -> verify
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """End-to-end round-trip with the two-key chain."""

    def test_countersign_writes_sig_and_meta(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, _pub = _make_pem_keypair(tmp_path, "acme")
        sig_path = countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        assert sig_path.exists()
        assert sig_path.name == CUSTOMER_SIG_FILENAME
        meta_path = bundle / CUSTOMER_META_FILENAME
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["org_name"] == "acme"
        assert meta["alg"] == "EdDSA"
        assert "BEGIN PUBLIC KEY" in meta["public_key_pem"]

    def test_verify_passes_with_trusted_key(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, pub = _make_pem_keypair(tmp_path, "acme")
        countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        trust = _make_trust_dir(tmp_path, [("acme", pub)])
        outcome = verify_customer_signature(bundle, trust_dir=trust)
        assert outcome.present is True
        assert outcome.valid is True
        assert outcome.matched_org == "acme"

    def test_full_verify_wheelhouse_passes(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, pub = _make_pem_keypair(tmp_path, "acme")
        countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        trust = _make_trust_dir(tmp_path, [("acme", pub)])
        report = verify_wheelhouse(
            bundle,
            verifier=None,
            require_signatures=False,
            require_customer_sig=True,
            customer_trust_dir=trust,
        )
        assert report.ok is True
        assert report.customer_signature_present is True
        assert report.customer_signature_ok is True
        assert report.customer_org == "acme"

    def test_default_org_name_is_key_stem(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, pub = _make_pem_keypair(tmp_path, "compliance-2026q2")
        countersign_bundle(bundle, customer_key_path=priv)  # no org_name
        trust = _make_trust_dir(tmp_path, [("compliance-2026q2", pub)])
        outcome = verify_customer_signature(bundle, trust_dir=trust)
        assert outcome.matched_org == "compliance-2026q2"


# ---------------------------------------------------------------------------
# Tampering detection
# ---------------------------------------------------------------------------


class TestTamperingDetection:
    """Modify the bundle after countersigning and confirm verify fails."""

    def test_modified_wheel_fails_sha_check(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, pub = _make_pem_keypair(tmp_path, "acme")
        countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        trust = _make_trust_dir(tmp_path, [("acme", pub)])

        # Tamper with a wheel after countersigning. The sha256 check
        # fires before the customer-sig path is even reached -- this is
        # the manifest-pinned chain doing its job.
        wheel = next(bundle.glob("pkg*.whl"))
        wheel.write_bytes(b"tampered-content")

        report = verify_wheelhouse(
            bundle,
            verifier=None,
            require_signatures=False,
            require_customer_sig=True,
            customer_trust_dir=trust,
        )
        assert report.ok is False
        assert any("sha256 mismatch" in failure for failure in report.failures)

    def test_modified_manifest_breaks_customer_sig(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, pub = _make_pem_keypair(tmp_path, "acme")
        countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        trust = _make_trust_dir(tmp_path, [("acme", pub)])

        # Editing the manifest invalidates the customer signature even
        # before we touch the underlying wheels.
        manifest_path = bundle / "MANIFEST.json"
        original = json.loads(manifest_path.read_text())
        original["version"] = "9.9.9-tampered"
        manifest_path.write_text(json.dumps(original, indent=2, sort_keys=True) + "\n")

        outcome = verify_customer_signature(bundle, trust_dir=trust)
        # The signature was made over the original manifest bytes; the
        # tampered manifest no longer matches => valid=False.
        assert outcome.present is True
        assert outcome.valid is False

    def test_tampered_signature_fails(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv, pub = _make_pem_keypair(tmp_path, "acme")
        sig_path = countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        trust = _make_trust_dir(tmp_path, [("acme", pub)])

        # Flip a byte in the signature -- valid Ed25519 sigs are 64
        # bytes so any single-byte change must reject.
        sig_bytes = bytearray(sig_path.read_bytes())
        sig_bytes[0] ^= 0xFF
        sig_path.write_bytes(bytes(sig_bytes))

        outcome = verify_customer_signature(bundle, trust_dir=trust)
        assert outcome.valid is False


# ---------------------------------------------------------------------------
# Require-customer-sig flag
# ---------------------------------------------------------------------------


class TestRequireCustomerSig:
    """Behaviour of the --require-customer-sig flag."""

    def test_absent_sig_passes_when_not_required(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        report = verify_wheelhouse(
            bundle,
            verifier=None,
            require_signatures=False,
            require_customer_sig=False,
        )
        assert report.ok is True
        assert report.customer_signature_present is False
        assert report.customer_signature_ok is None

    def test_absent_sig_fails_when_required(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        report = verify_wheelhouse(
            bundle,
            verifier=None,
            require_signatures=False,
            require_customer_sig=True,
        )
        assert report.ok is False
        assert any("missing customer signature" in failure for failure in report.failures)

    def test_present_sig_without_trust_fails_when_required(
        self,
        tmp_path: Path,
    ) -> None:
        bundle = _make_bundle(tmp_path)
        priv, _pub = _make_pem_keypair(tmp_path, "acme")
        countersign_bundle(bundle, customer_key_path=priv, org_name="acme")
        # No trust dir -- the sig is present but unverifiable.
        empty_trust = tmp_path / "empty-trust"
        empty_trust.mkdir()
        report = verify_wheelhouse(
            bundle,
            verifier=None,
            require_signatures=False,
            require_customer_sig=True,
            customer_trust_dir=empty_trust,
        )
        assert report.ok is False
        assert any("unverified" in failure for failure in report.failures)


# ---------------------------------------------------------------------------
# Multiple customer keys allowed
# ---------------------------------------------------------------------------


class TestMultipleTrustedKeys:
    """A trust dir with several keys accepts any matching signer."""

    def test_two_keys_one_matches(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv_a, pub_a = _make_pem_keypair(tmp_path, "compliance-officer-alice")
        _priv_b, pub_b = _make_pem_keypair(tmp_path, "compliance-officer-bob")
        # Sign with Alice's key; Bob's public key is ALSO in the trust
        # store but Alice should win the lookup.
        countersign_bundle(bundle, customer_key_path=priv_a, org_name="compliance-officer-alice")
        trust = _make_trust_dir(
            tmp_path,
            [
                ("compliance-officer-alice", pub_a),
                ("compliance-officer-bob", pub_b),
            ],
        )
        outcome = verify_customer_signature(bundle, trust_dir=trust)
        assert outcome.valid is True
        assert outcome.matched_org == "compliance-officer-alice"

    def test_no_match_fails_cleanly(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        priv_unknown, _ = _make_pem_keypair(tmp_path, "unknown")
        _, pub_a = _make_pem_keypair(tmp_path, "alice")
        countersign_bundle(bundle, customer_key_path=priv_unknown, org_name="unknown")
        # Trust store has Alice but the bundle was signed by 'unknown'.
        trust = _make_trust_dir(tmp_path, [("alice", pub_a)])
        outcome = verify_customer_signature(bundle, trust_dir=trust)
        assert outcome.valid is False
        assert "no key" in outcome.error

    def test_load_trust_store_skips_non_keys(self, tmp_path: Path) -> None:
        # The trust dir might contain README files / .DS_Store -- those
        # should be skipped silently rather than fail load.
        _priv, pub = _make_pem_keypair(tmp_path, "acme")
        trust = _make_trust_dir(tmp_path, [("acme", pub)])
        (trust / "README.md").write_text("operator note\n")
        # The README has neither .pem nor .pub suffix so it won't be
        # considered a key file. A non-PEM with .pem suffix would.
        (trust / "broken.pem").write_text("not a key")
        keys = load_trust_store(trust)
        assert "acme" in keys
        assert "broken" not in keys
        assert "README" not in keys


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Constructor + verify error surfaces."""

    def test_bundle_must_be_a_directory(self, tmp_path: Path) -> None:
        bogus = tmp_path / "bundle.tar"
        bogus.write_text("not a directory")
        priv, _ = _make_pem_keypair(tmp_path, "acme")
        with pytest.raises(CustomerCountersignError, match="not a directory"):
            countersign_bundle(bogus, customer_key_path=priv)

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        priv, _ = _make_pem_keypair(tmp_path, "acme")
        with pytest.raises(CustomerCountersignError, match="MANIFEST.json"):
            countersign_bundle(empty, customer_key_path=priv)

    def test_bad_private_key_raises(self, tmp_path: Path) -> None:
        bundle = _make_bundle(tmp_path)
        bad_key = tmp_path / "bad.pem"
        bad_key.write_bytes(b"-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n")
        with pytest.raises(CustomerCountersignError, match="invalid PEM"):
            countersign_bundle(bundle, customer_key_path=bad_key)

    def test_verify_with_no_manifest_returns_clear_error(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        outcome = verify_customer_signature(empty)
        assert outcome.present is False
        assert "MANIFEST.json" in outcome.error
