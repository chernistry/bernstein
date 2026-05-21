"""Integration: SOC 2 evidence pack pulls real per-control references.

Boots a minimal "run" by populating concrete artefacts under a temp
project root (audit chain, credential-scoping policy, capability matrix
runs, cluster-TLS log, wheelhouse verify result, run audit slices),
calls :func:`generate_audit_pack`, and asserts every declared
:class:`EvidenceSource` resolves to a non-empty reference.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bernstein.core.security.article12_bundle import emit_run_audit_event
from bernstein.core.security.audit_pack import (
    DEFAULT_EVIDENCE_SOURCES,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_STALE,
    EvidenceSource,
    generate_audit_pack,
    resolve_evidence_sources,
)


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """Skeleton project root with the directories the resolvers need."""
    (tmp_path / ".sdd" / "audit").mkdir(parents=True)
    (tmp_path / ".sdd" / "runtime").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def populated_project(project_root: Path) -> Path:
    """A project root with one concrete artefact per evidence source.

    Mirrors what a real Bernstein run would leave on disk: a daily audit
    log, a credential-scoping policy file, a capability-matrix run
    snapshot, a cluster-TLS validation log, a wheelhouse verify json,
    and one per-run audit slice.
    """
    base = project_root
    audit_key = b"x" * 32

    # --- audit chain (.sdd/audit/<date>.jsonl) ---
    audit_dir = base / ".sdd" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    chain_entry = {
        "timestamp": f"{today}T00:00:00.000000Z",
        "event_type": "task.created",
        "actor": "tester",
        "resource_type": "task",
        "resource_id": "T-1",
        "details": {},
        "prev_hmac": "0" * 64,
        "hmac": "abc" * 21 + "z",
    }
    (audit_dir / f"{today}.jsonl").write_text(json.dumps(chain_entry, sort_keys=True) + "\n")

    # --- policy_file: CC1.2 (CODE_OF_CONDUCT.md) ---
    (base / "CODE_OF_CONDUCT.md").write_text("# Code of Conduct\nIntegrity binds us.\n", encoding="utf-8")

    # --- policy_file: CC1.1 (board CoC review minutes) ---
    docs_dir = base / "docs" / "security"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "CODE_OF_CONDUCT_REVIEW.md").write_text(
        "# CoC review\nReviewed 2026-01-15 by board.\n",
        encoding="utf-8",
    )

    # --- policy_file: CC6.1 (credential_scoping.py) ---
    src_dir = base / "src" / "bernstein" / "core"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "credential_scoping.py").write_text(
        '"""Credential scoping policy."""\nDEFAULT_SCOPES = ()\n',
        encoding="utf-8",
    )

    # --- capability_matrix run snapshot ---
    cap_dir = base / ".sdd" / "runtime" / "spawn_capabilities"
    cap_dir.mkdir(parents=True, exist_ok=True)
    (cap_dir / "run-001.json").write_text(
        json.dumps({"tools": ["bash", "edit"], "violations": []}),
        encoding="utf-8",
    )

    # --- cluster_tls validation log ---
    tls_dir = base / ".sdd" / "runtime" / "cluster_tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    (tls_dir / "validate.log").write_text(
        "2026-01-15T10:00:00Z OK valid_until=2027-01-15\n",
        encoding="utf-8",
    )

    # --- wheelhouse verify ---
    wh_dir = base / ".sdd" / "runtime" / "wheelhouse"
    wh_dir.mkdir(parents=True, exist_ok=True)
    (wh_dir / "verify-2026-01-15.json").write_text(
        json.dumps({"wheels_checked": 42, "all_valid": True}),
        encoding="utf-8",
    )

    # --- run_log: per-run audit slice ---
    emit_run_audit_event(
        sdd_dir=base / ".sdd",
        run_id="run-soc2-001",
        event_type="task.created",
        actor="orchestrator",
        resource_type="task",
        resource_id="T-1",
        details={},
        audit_key=audit_key,
    )

    return base


class TestEvidenceResolution:
    """Verify every declared EvidenceSource resolves to non-empty content."""

    def test_default_sources_resolve_against_populated_project(
        self,
        populated_project: Path,
    ) -> None:
        resolved = resolve_evidence_sources(workdir=populated_project)
        # All declared sources should produce one resolved row.
        assert len(resolved) == len(DEFAULT_EVIDENCE_SOURCES)
        # No row should be empty (status==PENDING with PENDING_EVIDENCE
        # message) for the populated project.
        for entry in resolved:
            assert entry.status in {STATUS_OK, STATUS_STALE}, (
                f"{entry.source.control_id}/{entry.source.kind} resolved to {entry.status}: {entry.evidence_ref!r}"
            )
            assert entry.evidence_ref, "evidence_ref must be non-empty when status != PENDING"
            # Each kind must contribute *some* sha256 reference except
            # for the run_log digest, which always emits a sha256 over
            # the run summary.
            assert "sha256:" in entry.evidence_ref or entry.source.relpath in entry.evidence_ref

    def test_pending_sources_when_project_empty(self, project_root: Path) -> None:
        resolved = resolve_evidence_sources(workdir=project_root)
        # Empty project: at minimum the audit-chain, capability-matrix,
        # cluster-tls, wheelhouse, and run-log sources should report PENDING.
        pending_kinds = {r.source.kind for r in resolved if r.status == STATUS_PENDING}
        assert {
            "audit_chain",
            "capability_matrix",
            "tls_cert_log",
            "wheelhouse_verify",
            "run_log",
        }.issubset(pending_kinds)

    def test_include_runs_filters_run_log(self, populated_project: Path) -> None:
        # Far-future cutoff - the run_log resolver should drop the
        # in-tree slice and report PENDING.
        future = datetime.now(tz=UTC) + timedelta(days=365)
        resolved = resolve_evidence_sources(
            workdir=populated_project,
            include_since=future,
        )
        run_log_rows = [r for r in resolved if r.source.kind == "run_log"]
        assert run_log_rows, "expected at least one run_log evidence source"
        assert all(r.status == STATUS_PENDING for r in run_log_rows)

    def test_stale_threshold_flips_status(
        self,
        populated_project: Path,
    ) -> None:
        # Backdate the audit chain by 90 days; with default threshold of
        # 30 days the row must report STALE.
        chain_path = next((populated_project / ".sdd" / "audit").glob("*.jsonl"))
        old_mtime = (datetime.now(tz=UTC) - timedelta(days=90)).timestamp()
        import os

        os.utime(chain_path, (old_mtime, old_mtime))
        resolved = resolve_evidence_sources(workdir=populated_project)
        chain_rows = [r for r in resolved if r.source.kind == "audit_chain"]
        assert chain_rows
        assert any(r.status == STATUS_STALE for r in chain_rows)


class TestGenerateAuditPack:
    """Full pack generation against a populated project."""

    def test_pack_writes_markdown_and_manifest(
        self,
        populated_project: Path,
        tmp_path: Path,
    ) -> None:
        out = tmp_path / "out"
        result = generate_audit_pack(
            workdir=populated_project,
            output_dir=out,
            period_label="2026-Q1",
            stale_after_days=999,  # disable staleness for this assertion
        )

        assert result.markdown_path is not None
        assert result.manifest_path is not None
        assert result.markdown_path.exists()
        assert result.manifest_path.exists()

        body = result.markdown_path.read_text(encoding="utf-8")
        assert "# SOC 2 Evidence Checklist" in body
        # Every declared control must appear at least once.
        declared = {s.control_id for s in DEFAULT_EVIDENCE_SOURCES}
        for control_id in declared:
            assert control_id in body, f"{control_id} missing from markdown"
        # No row should still be the bare TBD marker.
        assert "evidence: TBD" not in body
        # OK count must dominate now that the project is populated.
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        ok_count = sum(1 for entry in manifest["evidence"] if entry["status"] == STATUS_OK)
        assert ok_count >= len(DEFAULT_EVIDENCE_SOURCES) - 1

    def test_manifest_includes_evidence_refs_per_control(
        self,
        populated_project: Path,
        tmp_path: Path,
    ) -> None:
        result = generate_audit_pack(
            workdir=populated_project,
            output_dir=tmp_path / "out",
            period_label="ci",
            stale_after_days=999,
        )
        evidence_rows: list[dict[str, Any]] = result.manifest["evidence"]
        for entry in evidence_rows:
            if entry["status"] == STATUS_OK:
                assert "sha256:" in entry["evidence_ref"] or entry["relpath"] in entry["evidence_ref"]
                assert entry["last_modified"] > 0
                assert entry["details"], f"OK row should carry details for {entry['control_id']}/{entry['kind']}"

    def test_custom_source_extension(
        self,
        populated_project: Path,
        tmp_path: Path,
    ) -> None:
        # Operators can supply their own EvidenceSource list - the
        # generator must drive the resolver against it.
        custom = (
            EvidenceSource(
                control_id="CUSTOM.1",
                kind="policy_file",
                relpath="CODE_OF_CONDUCT.md",
                description="Custom proof",
            ),
        )
        result = generate_audit_pack(
            workdir=populated_project,
            output_dir=tmp_path / "out",
            period_label="custom",
            sources=custom,
            stale_after_days=999,
        )
        assert len(result.resolved) == 1
        assert result.resolved[0].source.control_id == "CUSTOM.1"
        assert result.resolved[0].status == STATUS_OK
