"""Regression tests for ``bernstein cluster bootstrap-ca`` certificate extensions.

OpenSSL 3.x strict-mode parsing requires SubjectKeyIdentifier (SKI),
AuthorityKeyIdentifier (AKI), and KeyUsage extensions on leaf certs.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from bernstein.cli.commands.cluster_cmd import cluster_group


def _run_bootstrap(out_dir: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cluster_group,
        ["bootstrap-ca", "--out-dir", str(out_dir)],
    )
    assert result.exit_code == 0, f"bootstrap-ca failed: {result.output}\n{result.exception!r}"


def _load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


class TestBootstrapCaExtensions:
    """Verify CA and leaf certs include SKI/AKI/KeyUsage (OpenSSL 3.x strict)."""

    def test_ca_cert_has_ski_and_aki(self, tmp_path: Path) -> None:
        _run_bootstrap(tmp_path)
        ca = _load(tmp_path / "ca.crt")

        ski = ca.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER)
        aki = ca.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_KEY_IDENTIFIER)

        assert ski.value.digest, "CA cert missing SubjectKeyIdentifier digest"
        # Self-signed CA: AKI key identifier must match SKI digest.
        assert aki.value.key_identifier == ski.value.digest, (
            "CA AKI key identifier must match its own SKI (self-signed CA)"
        )

    def test_ca_cert_keeps_key_usage(self, tmp_path: Path) -> None:
        _run_bootstrap(tmp_path)
        ca = _load(tmp_path / "ca.crt")
        ku = ca.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
        assert ku.key_cert_sign is True
        assert ku.crl_sign is True
        assert ku.digital_signature is True

    @pytest.mark.parametrize("leaf_name", ["server", "node"])
    def test_leaf_cert_has_ski_aki_key_usage(self, tmp_path: Path, leaf_name: str) -> None:
        _run_bootstrap(tmp_path)
        ca = _load(tmp_path / "ca.crt")
        leaf = _load(tmp_path / f"{leaf_name}.crt")

        # SKI present
        ski = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER)
        assert ski.value.digest

        # AKI present and chains to CA's SKI
        ca_ski = ca.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_KEY_IDENTIFIER).value.digest
        aki = leaf.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_KEY_IDENTIFIER)
        assert aki.value.key_identifier == ca_ski, f"{leaf_name} AKI must reference CA SKI for chain validation"

        # KeyUsage with the correct flags
        ku_ext = leaf.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE)
        ku = ku_ext.value
        assert ku_ext.critical is True
        assert ku.digital_signature is True
        assert ku.key_encipherment is True
        assert ku.key_cert_sign is False
        assert ku.crl_sign is False

    def test_leaf_cert_keeps_extended_key_usage(self, tmp_path: Path) -> None:
        _run_bootstrap(tmp_path)
        server = _load(tmp_path / "server.crt")
        node = _load(tmp_path / "node.crt")

        server_eku = server.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
        node_eku = node.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
        assert x509.ExtendedKeyUsageOID.SERVER_AUTH in server_eku
        assert x509.ExtendedKeyUsageOID.CLIENT_AUTH in server_eku
        assert x509.ExtendedKeyUsageOID.SERVER_AUTH not in node_eku
        assert x509.ExtendedKeyUsageOID.CLIENT_AUTH in node_eku

    def test_openssl_x509_text_lists_new_extensions(self, tmp_path: Path) -> None:
        """Optional shell-out check: ``openssl x509 -text`` lists SKI + AKI."""
        if shutil.which("openssl") is None:
            pytest.skip("openssl binary not available")

        _run_bootstrap(tmp_path)
        for pem in ("ca.crt", "server.crt", "node.crt"):
            out = subprocess.run(
                ["openssl", "x509", "-in", str(tmp_path / pem), "-noout", "-text"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            assert "X509v3 Subject Key Identifier" in out, f"{pem} missing SKI in openssl text"
            assert "X509v3 Authority Key Identifier" in out, f"{pem} missing AKI in openssl text"
            if pem != "ca.crt":
                assert "X509v3 Key Usage" in out, f"{pem} missing KeyUsage in openssl text"
