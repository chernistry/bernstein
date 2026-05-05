"""Unit tests for the customer-key lineage signer (schema v2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bernstein.core.persistence.lineage import (
    AgentRef,
    ArtifactRef,
    LineageReader,
    LineageRecord,
    LineageWriter,
    canonical_record_bytes,
    decode_signature,
)
from bernstein.core.persistence.lineage_signer import (
    Ed25519FileKeySigner,
    Ed25519PublicKeyVerifier,
    LineageSigner,
    LineageSignerError,
    signer_from_config,
)


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


def _gen_raw_key(tmp_path: Path) -> Path:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out = tmp_path / "customer.raw"
    out.write_bytes(raw)
    return out


class TestEd25519FileKeySigner:
    def test_sign_and_verify_pem_round_trip(self, tmp_path: Path) -> None:
        key_path = _gen_pem_key(tmp_path)
        signer = Ed25519FileKeySigner.from_path(key_path)
        payload = b"hello regulator"
        sig = signer.sign(payload)
        verifier = Ed25519PublicKeyVerifier.from_raw(signer.public_key_bytes())
        assert verifier.verify(payload, sig)

    def test_sign_raw_key_format(self, tmp_path: Path) -> None:
        key_path = _gen_raw_key(tmp_path)
        signer = Ed25519FileKeySigner.from_path(key_path)
        sig = signer.sign(b"x")
        assert len(sig) == 64

    def test_missing_key_raises(self, tmp_path: Path) -> None:
        with pytest.raises(LineageSignerError, match="not found"):
            Ed25519FileKeySigner.from_path(tmp_path / "nope.pem")

    def test_bad_pem_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pem"
        bad.write_bytes(b"-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n")
        with pytest.raises(LineageSignerError, match="invalid PEM"):
            Ed25519FileKeySigner.from_path(bad)

    def test_bad_raw_length_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.raw"
        bad.write_bytes(b"too-short")
        with pytest.raises(LineageSignerError, match="32 bytes"):
            Ed25519FileKeySigner.from_path(bad)

    def test_verifier_rejects_tampered_payload(self, tmp_path: Path) -> None:
        signer = Ed25519FileKeySigner.from_path(_gen_pem_key(tmp_path))
        verifier = Ed25519PublicKeyVerifier.from_raw(signer.public_key_bytes())
        sig = signer.sign(b"original")
        assert not verifier.verify(b"tampered", sig)

    def test_signer_satisfies_protocol(self, tmp_path: Path) -> None:
        signer = Ed25519FileKeySigner.from_path(_gen_pem_key(tmp_path))
        assert isinstance(signer, LineageSigner)


class TestSignerFromConfig:
    def test_disabled_returns_none(self) -> None:
        assert signer_from_config(enabled=False, key_path=None) is None

    def test_enabled_without_key_raises(self) -> None:
        with pytest.raises(LineageSignerError, match="key_path"):
            signer_from_config(enabled=True, key_path=None)

    def test_unsupported_kind_raises(self, tmp_path: Path) -> None:
        key = _gen_pem_key(tmp_path)
        with pytest.raises(LineageSignerError, match="unsupported"):
            signer_from_config(enabled=True, key_path=str(key), key_kind="rsa-4096")

    def test_happy_path(self, tmp_path: Path) -> None:
        key = _gen_pem_key(tmp_path)
        signer = signer_from_config(enabled=True, key_path=str(key))
        assert signer is not None
        assert isinstance(signer, Ed25519FileKeySigner)


class TestSignedLineageWriter:
    def _record(self) -> LineageRecord:
        return LineageRecord(
            output_artifact=ArtifactRef(path="src/foo.py", sha256="a" * 64, line_start=1, line_end=10),
            inputs=[ArtifactRef(path="src/bar.py", sha256="b" * 64)],
            producer=AgentRef(agent_id="agent-1", run_id="run-1"),
            prompt_sha="c" * 64,
            model="claude-sonnet",
            cost_usd=0.01,
            tokens=100,
            timestamp=1700000000.0,
            regulatory_class="production_detection_rule",
        )

    def test_writer_signs_every_record(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        signer = Ed25519FileKeySigner.from_path(_gen_pem_key(tmp_path))
        writer = LineageWriter.for_run("run-1", sdd, signer=signer)

        writer.emit(self._record())
        writer.emit(self._record())

        reader = LineageReader(sdd)
        records = reader.lookup("src/foo.py")
        assert len(records) == 2
        assert all(r.customer_signature for r in records)

        verifier = Ed25519PublicKeyVerifier.from_raw(signer.public_key_bytes())
        for rec in records:
            assert rec.customer_signature is not None
            sig = decode_signature(rec.customer_signature)
            assert verifier.verify(canonical_record_bytes(rec), sig)

    def test_writer_without_signer_leaves_signature_none(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        writer = LineageWriter.for_run("run-1", sdd)
        writer.emit(self._record())
        reader = LineageReader(sdd)
        rec = reader.lookup("src/foo.py")[0]
        assert rec.customer_signature is None

    def test_default_regulatory_class_applied_when_unset(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        writer = LineageWriter.for_run(
            "run-1",
            sdd,
            default_regulatory_class="policy_edit",
        )
        unstamped = LineageRecord(
            output_artifact=ArtifactRef(path="src/foo.py", sha256="a" * 64),
            inputs=[],
            producer=AgentRef(agent_id="a", run_id="run-1"),
        )
        writer.emit(unstamped)
        rec = LineageReader(sdd).lookup("src/foo.py")[0]
        assert rec.regulatory_class == "policy_edit"

    def test_explicit_regulatory_class_wins_over_default(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        writer = LineageWriter.for_run("run-1", sdd, default_regulatory_class="policy_edit")
        writer.emit(self._record())  # explicit production_detection_rule
        rec = LineageReader(sdd).lookup("src/foo.py")[0]
        assert rec.regulatory_class == "production_detection_rule"


class TestSchemaVersionRoundTrip:
    def test_v2_record_round_trip(self, tmp_path: Path) -> None:
        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        signer = Ed25519FileKeySigner.from_path(_gen_pem_key(tmp_path))
        writer = LineageWriter.for_run("run-1", sdd, signer=signer)
        record = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a" * 64),
            inputs=[],
            producer=AgentRef(agent_id="a", run_id="run-1"),
            regulatory_class="remediation_playbook",
        )
        writer.emit(record)
        rec = LineageReader(sdd).lookup("x.py")[0]
        assert rec.schema_version == 2
        assert rec.regulatory_class == "remediation_playbook"
        assert rec.customer_signature is not None

    def test_v1_record_reads_back_with_v2_fields_as_none(self, tmp_path: Path) -> None:
        # Simulate a v1 WAL by writing through the WAL writer directly with
        # the legacy payload shape (no schema_version, no v2 fields).
        from bernstein.core.persistence.wal import WALWriter

        sdd = tmp_path / ".sdd"
        sdd.mkdir()
        wal = WALWriter(run_id="run-legacy", sdd_dir=sdd)
        wal.append(
            decision_type="lineage",
            inputs={
                "inputs": [],
                "producer": {"agent_id": "a", "run_id": "run-legacy", "tick_id": None},
                "prompt_sha": "deadbeef",
                "model": "claude-sonnet",
            },
            output={
                "output_artifact": {
                    "path": "src/x.py",
                    "sha256": "a" * 64,
                    "byte_start": None,
                    "byte_end": None,
                    "line_start": None,
                    "line_end": None,
                },
                "cost_usd": 0.01,
                "tokens": 100,
                "timestamp": 1.0,
            },
            actor="a",
        )
        rec = LineageReader(sdd).lookup("src/x.py", run_id="run-legacy")[0]
        assert rec.schema_version == 1
        assert rec.regulatory_class is None
        assert rec.customer_signature is None
        assert rec.prompt_sha == "deadbeef"

    def test_canonical_bytes_excludes_signature(self, tmp_path: Path) -> None:
        record = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a"),
            producer=AgentRef(agent_id="a", run_id="r"),
            customer_signature="should-not-affect-canonical",
        )
        record_no_sig = LineageRecord(
            output_artifact=ArtifactRef(path="x.py", sha256="a"),
            producer=AgentRef(agent_id="a", run_id="r"),
        )
        assert canonical_record_bytes(record) == canonical_record_bytes(record_no_sig)
