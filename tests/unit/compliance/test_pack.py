"""Tests for the EU AI Act Article 12 compliance pack builder."""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bernstein.core.compliance.pack import build_pack
from bernstein.core.lineage.entry import LineageEntry, canonicalise, entry_hash
from bernstein.core.lineage.identity import (
    AgentCard,
    generate_keypair,
    sign_detached,
    verify_detached,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _date_to_ns(d: str) -> int:
    """Convert YYYY-MM-DD to ns-since-epoch at 00:00:00 UTC."""
    from datetime import UTC, datetime

    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def _make_entry(*, path: str, content: str, agent_id: str, ts_ns: int) -> LineageEntry:
    return LineageEntry(
        v=1,
        artefact_path=path,
        artefact_kind="file",
        content_hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        parent_hashes=[],
        agent_id=agent_id,
        agent_card_kid=f"{agent_id}-kid",
        tool_call_id=f"tc-{ts_ns}",
        span_id=f"{ts_ns:016x}"[:16],
        ts_ns=ts_ns,
        operator_hmac="deadbeef",
    )


def _operator_key(tmp_path: Path) -> tuple[Path, str]:
    """Write an Ed25519 PEM PKCS#8 key for operator-side manifest signing."""
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "operator.key"
    key_path.write_bytes(pem)
    pub_pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return key_path, pub_pem


@pytest.fixture
def lineage_layout(tmp_path: Path) -> dict[str, Path]:
    """Lay out a minimal .sdd/lineage/ directory with two signed entries.

    One entry is INSIDE the [since, until] window; one is OUTSIDE.
    """
    lineage_dir = tmp_path / "lineage"
    signatures_dir = lineage_dir / "signatures"
    agent_cards_dir = tmp_path / "agents"
    lineage_dir.mkdir()
    signatures_dir.mkdir()
    agent_cards_dir.mkdir()

    # Single agent with one keypair.
    priv_pem, pub_pem = generate_keypair()
    agent_id = "agent:worker-1"
    kid = f"{agent_id}-kid"
    card = AgentCard(agent_id=agent_id, kid=kid, public_key_pem=pub_pem)
    (agent_cards_dir / f"{agent_id.replace(':', '_')}.json").write_text(
        json.dumps(asdict(card), sort_keys=True),
        encoding="utf-8",
    )

    in_window_ts = _date_to_ns("2026-03-15")
    out_window_ts = _date_to_ns("2025-12-31")

    entries = [
        _make_entry(
            path="src/in_window.py",
            content="hello-window",
            agent_id=agent_id,
            ts_ns=in_window_ts,
        ),
        _make_entry(
            path="src/outside.py",
            content="hello-outside",
            agent_id=agent_id,
            ts_ns=out_window_ts,
        ),
    ]

    log_path = lineage_dir / "log.jsonl"
    with log_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            entry_dict = asdict(entry)
            f.write(json.dumps(entry_dict, sort_keys=True) + "\n")

    # Write one .jws per entry under signatures/<entry_hash>.jws.
    for entry in entries:
        canonical = canonicalise(entry)
        h = entry_hash(entry)
        jws = sign_detached(canonical, priv_pem, kid=kid)
        # Note: filename is hash-only to keep flat structure.
        sig_path = signatures_dir / f"{h.split(':', 1)[1]}.jws"
        sig_path.write_text(jws, encoding="utf-8")

    return {
        "lineage_dir": lineage_dir,
        "agent_cards_dir": agent_cards_dir,
        "agent_card": agent_cards_dir / f"{agent_id.replace(':', '_')}.json",
        "log_path": log_path,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildPack:
    def test_zip_structure(self, tmp_path: Path, lineage_layout: dict[str, Path]) -> None:
        key_path, _pub_pem = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"

        result = build_pack(
            since=date(2026, 1, 1),
            until=date(2026, 5, 13),
            org="Acme",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )
        assert result == out_path
        assert out_path.exists()

        with zipfile.ZipFile(out_path) as zf:
            names = set(zf.namelist())

        required = {
            "README.md",
            "article12-evidence.pdf",
            "article12-evidence.csv",
            "lineage-log.jsonl",
            "verify-instructions.md",
            "pack-manifest.json",
            "pack-manifest.json.sig",
        }
        assert required <= names

        # signatures/ and agent-cards/ are folders -- must have at least one
        # file under each.
        assert any(n.startswith("signatures/") and n != "signatures/" for n in names)
        assert any(n.startswith("agent-cards/") and n != "agent-cards/" for n in names)

        # No unexpected top-level files.
        top_level = {n.split("/", 1)[0] for n in names if n}
        allowed_top = {
            "README.md",
            "article12-evidence.pdf",
            "article12-evidence.csv",
            "lineage-log.jsonl",
            "verify-instructions.md",
            "pack-manifest.json",
            "pack-manifest.json.sig",
            "signatures",
            "agent-cards",
        }
        extras = top_level - allowed_top
        assert not extras, f"unexpected top-level entries: {extras}"

    def test_log_filtered_to_window(
        self,
        tmp_path: Path,
        lineage_layout: dict[str, Path],
    ) -> None:
        key_path, _ = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"
        build_pack(
            since=date(2026, 1, 1),
            until=date(2026, 5, 13),
            org="Acme",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )

        with zipfile.ZipFile(out_path) as zf:
            log_lines = zf.read("lineage-log.jsonl").decode().splitlines()

        assert len(log_lines) == 1
        parsed = json.loads(log_lines[0])
        assert parsed["artefact_path"] == "src/in_window.py"

    def test_manifest_signature_verifies(
        self,
        tmp_path: Path,
        lineage_layout: dict[str, Path],
    ) -> None:
        key_path, pub_pem = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"
        build_pack(
            since=date(2026, 1, 1),
            until=date(2026, 5, 13),
            org="Acme",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )

        with zipfile.ZipFile(out_path) as zf:
            manifest_bytes = zf.read("pack-manifest.json")
            sig = zf.read("pack-manifest.json.sig").decode("ascii")

        manifest = json.loads(manifest_bytes)
        assert manifest["builder"]
        assert manifest["build_started_at"]
        assert manifest["build_finished_at"]
        assert isinstance(manifest["input_hashes"], dict)
        assert manifest["output_hash"].startswith("sha256:")

        # Operator-issued JWS verifies under the operator card (a synthetic
        # card minted from the pub_pem).
        card = AgentCard(
            agent_id="operator",
            kid=manifest["operator_kid"],
            public_key_pem=pub_pem,
        )
        assert verify_detached(manifest_bytes, sig, card)

    def test_all_jws_files_reference_real_entries(
        self,
        tmp_path: Path,
        lineage_layout: dict[str, Path],
    ) -> None:
        key_path, _ = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"
        build_pack(
            since=date(2026, 1, 1),
            until=date(2026, 5, 13),
            org="Acme",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )

        with zipfile.ZipFile(out_path) as zf:
            log = [json.loads(line) for line in zf.read("lineage-log.jsonl").decode().splitlines()]
            jws_files = [n for n in zf.namelist() if n.startswith("signatures/") and n.endswith(".jws")]

        # Compute every entry-hash from the (filtered) log.
        valid_hashes = set()
        for record in log:
            entry = LineageEntry(**record)
            valid_hashes.add(entry_hash(entry).split(":", 1)[1])

        assert len(jws_files) == len(valid_hashes)
        for jws_name in jws_files:
            hash_segment = Path(jws_name).stem
            assert hash_segment in valid_hashes, f"orphan signature: {jws_name}"

    def test_empty_period_still_builds(self, tmp_path: Path, lineage_layout: dict[str, Path]) -> None:
        key_path, _ = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"
        # Pick a window that excludes both fixture entries.
        result = build_pack(
            since=date(2030, 1, 1),
            until=date(2030, 12, 31),
            org="Empty",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )
        assert result == out_path
        with zipfile.ZipFile(out_path) as zf:
            log_lines = zf.read("lineage-log.jsonl").decode().splitlines()
        assert log_lines == []

    def test_log_bytes_are_canonical(self, tmp_path: Path, lineage_layout: dict[str, Path]) -> None:
        """Each lineage-log.jsonl line equals canonicalise(entry) byte-for-byte.

        The offline auditor binds verification to these exact bytes (issue
        #1871). A non-canonical write (default ``json.dumps`` spacing, key
        order other than sorted, missing terminator) would mean the bytes on
        disk no longer equal the canonical signed form, defeating byte-level
        tamper detection.
        """
        key_path, _ = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"
        build_pack(
            since=date(2026, 1, 1),
            until=date(2026, 5, 13),
            org="Acme",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )

        with zipfile.ZipFile(out_path) as zf:
            log_bytes = zf.read("lineage-log.jsonl")

        # File must end with a single trailing newline.
        assert log_bytes.endswith(b"\n")
        # Strict split on b"\n"; each non-empty record must be canonical.
        raw_lines = [ln for ln in log_bytes.split(b"\n") if ln]
        assert raw_lines, "expected at least one entry in window"
        for raw in raw_lines:
            entry = LineageEntry(**json.loads(raw))
            assert canonicalise(entry) == raw, (
                f"log line is not byte-canonical: on-disk={raw!r} canonical={canonicalise(entry)!r}"
            )

    def test_manifest_records_pack_format_version(self, tmp_path: Path, lineage_layout: dict[str, Path]) -> None:
        """The manifest records pack_format_version=2 so the offline verifier
        can dispatch the byte-binding rule (issue #1871)."""
        key_path, _ = _operator_key(tmp_path)
        out_path = tmp_path / "pack.zip"
        build_pack(
            since=date(2026, 1, 1),
            until=date(2026, 5, 13),
            org="Acme",
            lineage_dir=lineage_layout["lineage_dir"],
            agent_cards_dir=lineage_layout["agent_cards_dir"],
            output_path=out_path,
            operator_key_path=key_path,
        )
        with zipfile.ZipFile(out_path) as zf:
            manifest = json.loads(zf.read("pack-manifest.json"))
        assert manifest["pack_format_version"] == 2
