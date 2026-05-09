"""Tests for the pluggable KMS adapter protocol used by lineage v2.

Covers the :mod:`bernstein.core.security.lineage_kms` module:

* protocol contract -- file/env/hsm adapters all satisfy
  :class:`KMSAdapter` and the narrower
  :class:`bernstein.core.persistence.lineage_signer.LineageSigner`,
* round-trip -- file + env adapters produce verifiable Ed25519
  signatures for canonical lineage bytes,
* HSM stub -- :class:`HSMKMSAdapter` is constructible but raises
  :class:`NotImplementedError` cleanly on every operation,
* config dispatch -- ``kms_adapter_from_config`` and
  ``signer_from_config`` route by ``kind=file|env|hsm``.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bernstein.core.persistence.lineage_signer import (
    Ed25519PublicKeyVerifier,
    LineageSigner,
    LineageSignerError,
    signer_from_config,
)
from bernstein.core.security.lineage_kms import (
    EnvBasedKMSAdapter,
    FileBasedKMSAdapter,
    HSMKMSAdapter,
    KMSAdapter,
    kms_adapter_from_config,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _gen_pem_key(tmp_path: Path) -> Path:
    """Drop a fresh PEM PKCS#8 Ed25519 key in *tmp_path* and return its path."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out = tmp_path / "customer.pem"
    out.write_bytes(pem)
    return out


def _gen_pem_string() -> str:
    """Return a fresh PEM-encoded Ed25519 key as a string (for env-var tests)."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


# ---------------------------------------------------------------------------
# Protocol contract -- every concrete adapter satisfies KMSAdapter + LineageSigner
# ---------------------------------------------------------------------------


class TestKmsProtocolContract:
    """Every shipped adapter implements both protocols."""

    def test_file_adapter_satisfies_kms_protocol(self, tmp_path: Path) -> None:
        adapter = FileBasedKMSAdapter(_gen_pem_key(tmp_path))
        assert isinstance(adapter, KMSAdapter)

    def test_file_adapter_satisfies_lineage_signer(self, tmp_path: Path) -> None:
        adapter = FileBasedKMSAdapter(_gen_pem_key(tmp_path))
        assert isinstance(adapter, LineageSigner)

    def test_env_adapter_satisfies_kms_protocol(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LINEAGE_TEST_KEY", _gen_pem_string())
        adapter = EnvBasedKMSAdapter("LINEAGE_TEST_KEY", scrub_env=False)
        assert isinstance(adapter, KMSAdapter)

    def test_env_adapter_satisfies_lineage_signer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LINEAGE_TEST_KEY", _gen_pem_string())
        adapter = EnvBasedKMSAdapter("LINEAGE_TEST_KEY", scrub_env=False)
        assert isinstance(adapter, LineageSigner)

    def test_hsm_stub_satisfies_kms_protocol(self) -> None:
        # The stub is structurally KMSAdapter-compatible even though
        # every method raises -- runtime_checkable Protocol checks
        # signatures, not behaviour.
        stub = HSMKMSAdapter("pkcs11:token=t1;object=key1")
        assert isinstance(stub, KMSAdapter)

    def test_hsm_stub_satisfies_lineage_signer(self) -> None:
        stub = HSMKMSAdapter("pkcs11:token=t1;object=key1")
        assert isinstance(stub, LineageSigner)


# ---------------------------------------------------------------------------
# File-backed adapter -- end-to-end signing + JWK shape
# ---------------------------------------------------------------------------


class TestFileBasedKmsAdapter:
    """File adapter signs verifiable Ed25519 signatures and emits a JWK."""

    def test_round_trip_pem(self, tmp_path: Path) -> None:
        adapter = FileBasedKMSAdapter(_gen_pem_key(tmp_path))
        payload = b"canonical lineage record bytes"
        sig = adapter.sign(payload)
        # Pull the JWK and reconstruct the public key from it.
        jwk = adapter.public_key_jwk()
        assert jwk["kty"] == "OKP"
        assert jwk["crv"] == "Ed25519"
        assert jwk["alg"] == "EdDSA"
        # Decode the x coordinate and verify the signature with the
        # paired Ed25519PublicKeyVerifier path used by lineage_verify.
        raw_pub = base64.urlsafe_b64decode(jwk["x"] + "==")
        verifier = Ed25519PublicKeyVerifier.from_raw(raw_pub)
        assert verifier.verify(payload, sig)

    def test_default_kid_is_filename(self, tmp_path: Path) -> None:
        adapter = FileBasedKMSAdapter(_gen_pem_key(tmp_path))
        assert adapter.public_key_jwk()["kid"] == "customer.pem"

    def test_kid_override(self, tmp_path: Path) -> None:
        adapter = FileBasedKMSAdapter(_gen_pem_key(tmp_path), kid="ops-team-2026Q2")
        assert adapter.public_key_jwk()["kid"] == "ops-team-2026Q2"

    def test_missing_key_raises_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(LineageSignerError, match="not found"):
            FileBasedKMSAdapter(tmp_path / "nope.pem")


# ---------------------------------------------------------------------------
# Env-backed adapter -- PEM, escaped PEM, raw, rawb64
# ---------------------------------------------------------------------------


class TestEnvBasedKmsAdapter:
    """Env adapter accepts every documented payload format."""

    def test_round_trip_pem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINEAGE_K8S_KEY", _gen_pem_string())
        adapter = EnvBasedKMSAdapter("LINEAGE_K8S_KEY", scrub_env=False)
        sig = adapter.sign(b"hello")
        assert len(sig) == 64  # Ed25519 sig is always 64 bytes
        jwk = adapter.public_key_jwk()
        raw_pub = base64.urlsafe_b64decode(jwk["x"] + "==")
        assert Ed25519PublicKeyVerifier.from_raw(raw_pub).verify(b"hello", sig)

    def test_round_trip_escaped_newlines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # K8s ConfigMap-style flatten replaces literal newlines with \\n.
        flattened = _gen_pem_string().replace("\n", "\\n")
        monkeypatch.setenv("LINEAGE_FLAT_KEY", flattened)
        adapter = EnvBasedKMSAdapter("LINEAGE_FLAT_KEY", scrub_env=False)
        sig = adapter.sign(b"x")
        assert len(sig) == 64

    def test_round_trip_raw_hex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        monkeypatch.setenv("LINEAGE_RAW_HEX", "raw:" + raw.hex())
        adapter = EnvBasedKMSAdapter("LINEAGE_RAW_HEX", scrub_env=False)
        sig = adapter.sign(b"y")
        assert len(sig) == 64

    def test_round_trip_raw_b64(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        monkeypatch.setenv(
            "LINEAGE_RAW_B64",
            "rawb64:" + base64.b64encode(raw).decode("ascii"),
        )
        adapter = EnvBasedKMSAdapter("LINEAGE_RAW_B64", scrub_env=False)
        sig = adapter.sign(b"z")
        assert len(sig) == 64

    def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LINEAGE_ABSENT_KEY", raising=False)
        with pytest.raises(LineageSignerError, match="not set"):
            EnvBasedKMSAdapter("LINEAGE_ABSENT_KEY")

    def test_empty_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINEAGE_EMPTY_KEY", "   ")
        with pytest.raises(LineageSignerError, match="not set or empty"):
            EnvBasedKMSAdapter("LINEAGE_EMPTY_KEY")

    def test_bad_pem_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "LINEAGE_BAD_KEY",
            "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----\n",
        )
        with pytest.raises(LineageSignerError, match="invalid PEM"):
            EnvBasedKMSAdapter("LINEAGE_BAD_KEY")

    def test_bad_raw_hex_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINEAGE_BAD_HEX", "raw:zzzz-not-hex")
        with pytest.raises(LineageSignerError, match="not valid hex"):
            EnvBasedKMSAdapter("LINEAGE_BAD_HEX")

    def test_scrub_env_clears_var_after_load(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LINEAGE_SCRUBBED", _gen_pem_string())
        EnvBasedKMSAdapter("LINEAGE_SCRUBBED", scrub_env=True)
        # The constructor should have removed the env var so a forked
        # subprocess does not inherit the secret.
        assert "LINEAGE_SCRUBBED" not in os.environ

    def test_scrub_env_off_keeps_var(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LINEAGE_KEPT", _gen_pem_string())
        EnvBasedKMSAdapter("LINEAGE_KEPT", scrub_env=False)
        assert "LINEAGE_KEPT" in os.environ


# ---------------------------------------------------------------------------
# HSM stub -- documented NotImplementedError surface
# ---------------------------------------------------------------------------


class TestHsmKmsAdapter:
    """The HSM adapter is constructible but raises on every op."""

    def test_constructor_is_pure(self) -> None:
        # No I/O on construction so a misconfigured HSM URI never
        # blocks orchestrator startup -- the failure surfaces at the
        # first sign() call (or at the verifier-side public-key fetch).
        stub = HSMKMSAdapter("pkcs11:token=t1;object=lineage-key")
        assert stub.token_uri == "pkcs11:token=t1;object=lineage-key"

    def test_sign_raises_with_pkcs11_pointer(self) -> None:
        stub = HSMKMSAdapter("pkcs11:token=t1;object=lineage-key")
        with pytest.raises(NotImplementedError, match="PKCS#11"):
            stub.sign(b"payload")

    def test_public_key_jwk_raises_with_token_uri(self) -> None:
        stub = HSMKMSAdapter("pkcs11:token=t1;object=lineage-key")
        with pytest.raises(NotImplementedError, match="lineage-key"):
            stub.public_key_jwk()


# ---------------------------------------------------------------------------
# Config dispatch -- kms_adapter_from_config + signer_from_config kms path
# ---------------------------------------------------------------------------


class TestConfigDispatch:
    """Config dispatchers route to the right adapter."""

    def test_disabled_returns_none(self) -> None:
        assert kms_adapter_from_config(enabled=False) is None

    def test_file_kind_returns_file_adapter(self, tmp_path: Path) -> None:
        adapter = kms_adapter_from_config(
            enabled=True,
            kind="file",
            key_path=str(_gen_pem_key(tmp_path)),
        )
        assert isinstance(adapter, FileBasedKMSAdapter)

    def test_env_kind_returns_env_adapter(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LINEAGE_DISP_KEY", _gen_pem_string())
        adapter = kms_adapter_from_config(
            enabled=True,
            kind="env",
            env_var="LINEAGE_DISP_KEY",
        )
        assert isinstance(adapter, EnvBasedKMSAdapter)

    def test_hsm_kind_returns_stub(self) -> None:
        adapter = kms_adapter_from_config(
            enabled=True,
            kind="hsm",
            token_uri="pkcs11:token=stub",
        )
        assert isinstance(adapter, HSMKMSAdapter)

    def test_file_kind_without_path_raises(self) -> None:
        with pytest.raises(LineageSignerError, match="kms_adapter_key_path"):
            kms_adapter_from_config(enabled=True, kind="file")

    def test_env_kind_without_var_raises(self) -> None:
        with pytest.raises(LineageSignerError, match="kms_adapter_env_var"):
            kms_adapter_from_config(enabled=True, kind="env")

    def test_hsm_kind_without_token_raises(self) -> None:
        with pytest.raises(LineageSignerError, match="kms_adapter_token_uri"):
            kms_adapter_from_config(enabled=True, kind="hsm")

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(LineageSignerError, match="unsupported"):
            kms_adapter_from_config(enabled=True, kind="kerberos")

    def test_signer_from_config_phase2_file(self, tmp_path: Path) -> None:
        signer = signer_from_config(
            enabled=True,
            kms_adapter="file",
            key_path=str(_gen_pem_key(tmp_path)),
        )
        assert isinstance(signer, FileBasedKMSAdapter)

    def test_signer_from_config_phase2_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("LINEAGE_PHASE2_KEY", _gen_pem_string())
        signer = signer_from_config(
            enabled=True,
            kms_adapter="env",
            kms_env_var="LINEAGE_PHASE2_KEY",
        )
        assert isinstance(signer, EnvBasedKMSAdapter)

    def test_signer_from_config_phase2_hsm(self) -> None:
        signer = signer_from_config(
            enabled=True,
            kms_adapter="hsm",
            kms_token_uri="pkcs11:token=stub",
        )
        assert isinstance(signer, HSMKMSAdapter)
        # Confirm sign() still raises cleanly.
        with pytest.raises(NotImplementedError):
            signer.sign(b"payload")

    def test_signer_from_config_phase1_back_compat(self, tmp_path: Path) -> None:
        # Old callers without kms_adapter still get a file-backed signer.
        signer = signer_from_config(
            enabled=True,
            key_path=str(_gen_pem_key(tmp_path)),
        )
        # Phase-1 returns Ed25519FileKeySigner directly (not the new
        # FileBasedKMSAdapter) so the lineage writer continues to walk
        # the original code path. Both implement LineageSigner.
        assert isinstance(signer, LineageSigner)
