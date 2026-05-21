"""Integration tests for portable receipt export + offline verify (#1799).

Round-trips:

1. ``export_receipt`` writes a tarball.
2. ``verify_receipt`` reads it on a host that has *no* journal directory.
3. The walked head matches the head the exporter recorded in the
   manifest. The receipt is portable.

Also exercises the signed flow with an Ed25519 file key.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from bernstein.core.persistence.journal import Journal
from bernstein.core.persistence.journal_export import (
    export_receipt,
    verify_receipt,
)
from bernstein.core.persistence.journal_publish import (
    RedactionPolicy,
    publish_receipt,
)


def _build_journal(agent_dir: Path, n_steps: int = 3) -> str:
    journal = Journal.open(agent_dir)
    for i in range(n_steps):
        journal.append(
            input_hash=f"in-{i}",
            model="m1",
            prompt=f"prompt {i}",
            tool_call={"name": "noop"},
            tool_result={"ok": True},
        )
    head = journal.head_hash
    journal.close()
    return head


def test_export_then_verify_offline(tmp_path: Path) -> None:
    agent_dir = tmp_path / "src" / "agent-1"
    head = _build_journal(agent_dir, n_steps=4)

    receipt = tmp_path / "receipts" / "agent-1.tar"
    result = export_receipt(agent_dir, receipt, agent_id="agent-1")
    assert result.head_hash == head

    # Move the source out of reach to prove offline verification.
    agent_dir.rename(tmp_path / "src" / "moved-away")

    v = verify_receipt(receipt, expected_head=head)
    assert v.ok, v.errors
    assert v.head_hash == head
    assert v.steps == 4


def test_signed_receipt_round_trip(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    # Generate a fresh signing keypair.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from bernstein.core.persistence.lineage_signer import (
        Ed25519FileKeySigner,
        Ed25519PublicKeyVerifier,
    )

    private_key = Ed25519PrivateKey.from_private_bytes(secrets.token_bytes(32))
    key_path = tmp_path / "sig.key"
    from cryptography.hazmat.primitives import serialization

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(pem)

    signer = Ed25519FileKeySigner.from_path(key_path)
    public_key = private_key.public_key()
    verifier = Ed25519PublicKeyVerifier(public_key)

    agent_dir = tmp_path / "agent"
    head = _build_journal(agent_dir, n_steps=2)

    receipt = tmp_path / "signed.tar"
    result = export_receipt(agent_dir, receipt, agent_id="agent", signer=signer)
    assert result.signed

    v = verify_receipt(receipt, expected_head=head, verifier=verifier)
    assert v.ok, v.errors
    assert v.signed


def test_redacted_publish_still_offline_verifies(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    journal = Journal.open(agent_dir)
    journal.append(
        input_hash="aa",
        model="m1",
        prompt="confidential: token=abcd",
        tool_result={"stdout": "confidential output"},
    )
    journal.append(
        input_hash="bb",
        model="m1",
        prompt="more confidential",
        tool_result={"stdout": "more"},
    )
    journal.close()

    receipt = tmp_path / "published.tar"
    result = publish_receipt(
        agent_dir,
        receipt,
        agent_id="agent",
        policy=RedactionPolicy.default(),
        opt_in=True,
    )
    # Original head and published head differ (chain re-anchored).
    assert result.head_hash != result.original_head_hash

    v = verify_receipt(receipt, expected_head=result.head_hash)
    assert v.ok, v.errors

    # Sensitive cleartext must not survive into the published receipt.
    blob = receipt.read_bytes()
    assert b"confidential" not in blob
    assert b"token=abcd" not in blob
