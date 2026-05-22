"""Security tests for Merkle second-preimage / tamper weaknesses.

Covers two reported defects:

* GH-1844 - the audit seal leaf must bind the *whole* file, not just the
  last JSONL line's stored ``hmac``. A byte change in any non-final line
  must surface as ``TAMPERED`` from :func:`verify_merkle`.

* GH-1854 - both Merkle-root builders must be collision-resistant under
  last-node duplication and must domain-separate leaf hashes from
  internal-node hashes (RFC 6962 style: ``H(0x00 || leaf)`` for leaves,
  ``H(0x01 || left || right)`` for internal nodes). Duplicating the final
  leaf must not reproduce the un-duplicated root, and an internal-node
  digest must not be reusable as a leaf to reproduce a different tree's
  root.

These properties are the verifiable core of the audit substrate, so the
assertions prove the security guarantee empirically (tamper -> detected;
distinct leaf sets -> distinct roots) rather than restating the docstring.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import TYPE_CHECKING

from bernstein.core.persistence.merkle import (
    build_merkle_tree,
    compute_seal,
    save_seal,
    verify_merkle,
)
from bernstein.core.security.audit import AuditLog
from bernstein.core.security.compliance_report import compute_merkle_root

if TYPE_CHECKING:
    from pathlib import Path


def _event(hmac: str) -> dict[str, object]:
    return {"event_type": "task.complete", "hmac": hmac}


def _real_chained_log(audit_dir: Path, count: int) -> bytes:
    """Write *count* genuinely HMAC-chained events under *audit_dir*.

    Uses a real :class:`AuditLog` with an in-test key so the seal's chain
    verification passes; this proves the Merkle leaf (not just the HMAC
    chain) catches subsequent tampering. Returns the in-test key so the
    caller can pass it to :func:`compute_seal`.
    """
    key = secrets.token_bytes(32)
    log = AuditLog(audit_dir, key=key)
    for i in range(count):
        log.log(
            event_type="task.complete",
            actor="agent-1",
            resource_type="task",
            resource_id=f"t-{i}",
            details={"i": i},
        )
    return key


# ---------------------------------------------------------------------------
# GH-1844: mid-file tamper must be detected by the Merkle seal
# ---------------------------------------------------------------------------


class TestMidFileTamperDetection:
    def _seal_chained_file(self, tmp_path: Path) -> tuple[Path, Path, Path, list[bytes]]:
        """Seal a real 3-event HMAC-chained log; return its file + lines."""
        audit = tmp_path / "audit"
        merkle = audit / "merkle"
        key = _real_chained_log(audit, count=3)
        # AuditLog names the file by today's UTC date; resolve it.
        target = sorted(audit.glob("*.jsonl"))[0]
        lines = target.read_bytes().rstrip(b"\n").split(b"\n")
        assert len(lines) == 3, "fixture expected a single 3-line daily file"

        _, seal = compute_seal(audit, key=key)
        save_seal(seal, merkle)
        # Sanity: the seal verifies clean before any tamper.
        assert verify_merkle(audit, merkle).valid
        return audit, merkle, target, lines

    def test_non_final_line_byte_flip_detected(self, tmp_path: Path) -> None:
        """Flipping a byte in a non-final line (last line intact) trips TAMPERED.

        This is the exact case the old last-line-hmac leaf missed: the
        final record is untouched, so a last-line-only leaf would still
        match the seal.
        """
        audit, merkle, target, lines = self._seal_chained_file(tmp_path)
        name = target.name

        middle = bytearray(lines[1])
        middle[5] ^= 0x01  # flip a byte well inside the middle record
        lines[1] = bytes(middle)
        target.write_bytes(b"\n".join(lines) + b"\n")

        result = verify_merkle(audit, merkle)
        assert not result.valid
        assert any("TAMPERED" in e and name in e for e in result.errors)

    def test_first_line_byte_flip_detected(self, tmp_path: Path) -> None:
        """A single-byte flip in the first line must trip TAMPERED."""
        audit, merkle, target, lines = self._seal_chained_file(tmp_path)
        name = target.name

        first = bytearray(lines[0])
        first[5] ^= 0x01
        lines[0] = bytes(first)
        target.write_bytes(b"\n".join(lines) + b"\n")

        result = verify_merkle(audit, merkle)
        assert not result.valid
        assert any("TAMPERED" in e and name in e for e in result.errors)

    def test_final_line_tamper_still_detected(self, tmp_path: Path) -> None:
        """The previously-covered last-line case must remain detected."""
        audit, merkle, target, lines = self._seal_chained_file(tmp_path)
        name = target.name

        last = bytearray(lines[-1])
        last[5] ^= 0x01
        lines[-1] = bytes(last)
        target.write_bytes(b"\n".join(lines) + b"\n")

        result = verify_merkle(audit, merkle)
        assert not result.valid
        assert any("TAMPERED" in e and name in e for e in result.errors)

    def test_clean_roundtrip_still_valid(self, tmp_path: Path) -> None:
        """An untouched dir seals and verifies clean under the new scheme."""
        audit, merkle, _target, _ = self._seal_chained_file(tmp_path)
        result = verify_merkle(audit, merkle)
        assert result.valid
        assert result.errors == []

    def test_empty_file_stable_and_verifies(self, tmp_path: Path) -> None:
        """An empty/whitespace daily file gets a stable leaf and verifies clean."""
        from bernstein.core.persistence.merkle import file_leaf_hash

        audit = tmp_path / "audit"
        audit.mkdir()
        merkle = audit / "merkle"
        empty = audit / "2026-04-01.jsonl"
        empty.write_text("   \n")

        # A whitespace-only file maps to a stable, reproducible leaf.
        assert file_leaf_hash(empty) == file_leaf_hash(empty)

        # No HMAC-chained records present, so disable the chain precheck and
        # exercise the Merkle leaf/verify path directly.
        _, seal = compute_seal(audit, verify_chain=False)
        save_seal(seal, merkle)

        result = verify_merkle(audit, merkle)
        assert result.valid, result.errors


# ---------------------------------------------------------------------------
# GH-1854: second-preimage / duplication weakness in build_merkle_tree
# ---------------------------------------------------------------------------


class TestBuildMerkleTreeSecondPreimage:
    def test_duplicated_last_leaf_differs(self) -> None:
        """[A,B,C] and [A,B,C,C] must produce different roots."""
        abc = build_merkle_tree([("a", "A"), ("b", "B"), ("c", "C")]).root.hash
        abcc = build_merkle_tree(
            [("a", "A"), ("b", "B"), ("c", "C"), ("d", "C")],
        ).root.hash
        assert abc != abcc

    def test_determinism_preserved(self) -> None:
        leaves = [("a", "x"), ("b", "y"), ("c", "z")]
        assert build_merkle_tree(leaves).root.hash == build_merkle_tree(leaves).root.hash

    def test_order_still_matters(self) -> None:
        t1 = build_merkle_tree([("a", "x"), ("b", "y")]).root.hash
        t2 = build_merkle_tree([("b", "y"), ("a", "x")]).root.hash
        assert t1 != t2


# ---------------------------------------------------------------------------
# GH-1854: duplication + domain separation in compute_merkle_root
# ---------------------------------------------------------------------------


class TestComputeMerkleRootSecondPreimage:
    def test_duplicated_last_leaf_differs(self) -> None:
        """[A,B,C] and [A,B,C,C] event sets must produce different roots."""
        three = compute_merkle_root([_event("a"), _event("b"), _event("c")])
        four = compute_merkle_root([_event("a"), _event("b"), _event("c"), _event("c")])
        assert three != four

    def test_internal_node_not_reusable_as_leaf(self) -> None:
        """An internal-node digest must not reproduce a 2-leaf root.

        Without leaf/internal domain separation, the root over hmacs
        {a, b} equals ``H(H(a) || H(b))`` and an attacker can present a
        single leaf whose hmac is that internal digest to forge a tree of
        a different shape. Domain separation (0x00 vs 0x01) breaks this.
        """
        leaf_a = hashlib.sha256(b"a").hexdigest()
        leaf_b = hashlib.sha256(b"b").hexdigest()
        raw_internal = hashlib.sha256((leaf_a + leaf_b).encode()).hexdigest()

        root_two = compute_merkle_root([_event("a"), _event("b")])
        # The forged single leaf carrying the raw internal concat as its
        # hmac must NOT reproduce the two-leaf root.
        root_forged = compute_merkle_root([_event(raw_internal)])
        assert root_two != raw_internal
        assert root_two != root_forged

    def test_determinism_regardless_of_order(self) -> None:
        a = compute_merkle_root([_event("xxx"), _event("yyy")])
        b = compute_merkle_root([_event("yyy"), _event("xxx")])
        assert a == b

    def test_empty_events_stable(self) -> None:
        assert compute_merkle_root([]) == compute_merkle_root([])


# ---------------------------------------------------------------------------
# Backward compatibility: pre-hardening (v1) seals still verify
# ---------------------------------------------------------------------------


class TestLegacySchemeVerification:
    def test_v1_seal_verifies_under_v1_rules(self, tmp_path: Path) -> None:
        """A seal recorded under scheme v1 verifies via the legacy leaf rule.

        Operators with seals written before the hardening must still be able
        to verify them; verification dispatches on the seal's recorded
        scheme rather than always recomputing under v2.
        """
        from bernstein.core.persistence.merkle import (
            _legacy_file_leaf_hash,
            build_merkle_tree,
            save_seal,
        )

        audit = tmp_path / "audit"
        audit.mkdir()
        merkle = audit / "merkle"
        f1 = audit / "2026-03-28.jsonl"
        f2 = audit / "2026-03-29.jsonl"
        f1.write_text(json.dumps({"event": "a", "hmac": "h1"}) + "\n")
        f2.write_text(json.dumps({"event": "b", "hmac": "h2"}) + "\n")

        # Hand-build a v1 seal exactly as the pre-fix code would have.
        leaf_hashes = [
            (f1.name, _legacy_file_leaf_hash(f1.read_bytes())),
            (f2.name, _legacy_file_leaf_hash(f2.read_bytes())),
        ]
        tree = build_merkle_tree(leaf_hashes, scheme=1)
        v1_seal: dict[str, object] = {
            "root_hash": tree.root.hash,
            "algorithm": "sha256",
            "scheme": 1,
            "leaf_count": tree.leaf_count,
            "leaves": [{"file": n, "hash": h} for n, h in leaf_hashes],
            "sealed_at": 1.0,
            "sealed_at_iso": "2026-03-29T00:00:00Z",
        }
        save_seal(v1_seal, merkle)

        # The v1 seal verifies clean under its own rules.
        assert verify_merkle(audit, merkle).valid

    def test_seal_without_scheme_field_treated_as_v1(self, tmp_path: Path) -> None:
        """A seal predating the ``scheme`` field defaults to the v1 rule."""
        from bernstein.core.persistence.merkle import (
            _legacy_file_leaf_hash,
            build_merkle_tree,
            save_seal,
        )

        audit = tmp_path / "audit"
        audit.mkdir()
        merkle = audit / "merkle"
        f1 = audit / "2026-03-28.jsonl"
        f1.write_text(json.dumps({"event": "a", "hmac": "h1"}) + "\n")

        leaf_hashes = [(f1.name, _legacy_file_leaf_hash(f1.read_bytes()))]
        tree = build_merkle_tree(leaf_hashes, scheme=1)
        # No "scheme" key at all - the verifier must assume v1.
        seal: dict[str, object] = {
            "root_hash": tree.root.hash,
            "algorithm": "sha256",
            "leaf_count": tree.leaf_count,
            "leaves": [{"file": n, "hash": h} for n, h in leaf_hashes],
            "sealed_at": 1.0,
            "sealed_at_iso": "2026-03-29T00:00:00Z",
        }
        save_seal(seal, merkle)

        assert verify_merkle(audit, merkle).valid
