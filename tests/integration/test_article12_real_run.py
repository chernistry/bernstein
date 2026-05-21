"""Integration: assemble_from_run against a real per-run audit chain.

Boots a tiny in-process orchestrator surface (audit emit + lineage
writer), produces real bytes through the production HMAC chain, and
walks them back through :func:`assemble_from_run` + :func:`verify_bundle`.

No mocks for the audit or lineage writers - the chain that signs the
events is the same chain the production orchestrator would use.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bernstein.core.persistence.lineage import (
    AgentRef,
    ArtifactRef,
    LineageRecord,
    LineageWriter,
)
from bernstein.core.security.article12_bundle import (
    ChainBreakError,
    assemble_from_run,
    emit_run_audit_event,
    verify_bundle,
)


@pytest.fixture()
def audit_key() -> bytes:
    """Fixed key so the test does not touch the operator's keychain."""
    return b"x" * 32


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Project workspace with an empty ``.sdd`` root."""
    (tmp_path / ".sdd").mkdir()
    return tmp_path


@pytest.fixture()
def run_id() -> str:
    return "run-integration-001"


def _seed_run(
    *,
    sdd_dir: Path,
    run_id: str,
    audit_key: bytes,
    event_count: int = 3,
    base_time: datetime,
) -> list[dict]:
    """Emit *event_count* HMAC-chained audit events for *run_id*."""
    events = []
    for idx in range(event_count):
        entry = emit_run_audit_event(
            sdd_dir=sdd_dir,
            run_id=run_id,
            event_type=f"task.event_{idx}",
            actor="orchestrator",
            resource_type="task",
            resource_id=f"T-{idx:03d}",
            details={"idx": idx, "ts": base_time.timestamp() + idx},
            audit_key=audit_key,
        )
        events.append(entry)
    return events


def _seed_lineage(
    *,
    sdd_dir: Path,
    run_id: str,
    base_time: datetime,
    count: int = 2,
) -> list[LineageRecord]:
    """Append *count* lineage records to the run WAL via the real writer."""
    writer = LineageWriter.for_run(run_id=run_id, sdd_dir=sdd_dir)
    records = []
    for idx in range(count):
        record = LineageRecord(
            output_artifact=ArtifactRef(path=f"src/feature_{idx}.py", sha256=f"{idx:064d}"),
            inputs=[ArtifactRef(path="prompts/system.md", sha256="0" * 64)],
            producer=AgentRef(agent_id="claude-code", run_id=run_id, tick_id=f"t-{idx}"),
            prompt_sha="cafe" * 16,
            model="claude-sonnet-4.5",
            cost_usd=0.01 * (idx + 1),
            tokens=100 * (idx + 1),
            timestamp=base_time.timestamp() + idx,
            regulatory_class="high-risk",
        )
        writer.emit(record)
        records.append(record)
    return records


class TestAssembleFromRun:
    """End-to-end exercise of the runtime audit chain integration."""

    def test_real_run_produces_verifiable_bundle(
        self,
        workspace: Path,
        run_id: str,
        audit_key: bytes,
    ) -> None:
        sdd_dir = workspace / ".sdd"
        base_time = datetime.now(tz=UTC)
        _seed_run(sdd_dir=sdd_dir, run_id=run_id, audit_key=audit_key, base_time=base_time)
        _seed_lineage(sdd_dir=sdd_dir, run_id=run_id, base_time=base_time, count=2)

        since = base_time - timedelta(minutes=1)
        until = base_time + timedelta(minutes=5)

        result = assemble_from_run(
            run_id=run_id,
            since=since,
            until=until,
            sdd_dir=sdd_dir,
            workdir=workspace,
            risk_class="high",
            audit_key=audit_key,
        )

        assert result.bundle.event_count == 3
        assert result.chain_event_count == 3
        assert result.catalog_artefact_count == 2
        assert result.bundle.archive_path is not None
        assert result.bundle.archive_path.exists()

        verification = verify_bundle(result.bundle.archive_path)
        assert verification.ok, verification.errors
        assert verification.manifest["run_id"] == run_id
        assert verification.manifest["lineage_artefact_count"] == 2
        assert verification.manifest["risk_class"] == "high"
        assert verification.manifest["chain_anchor"] != "0" * 64

    def test_data_catalog_includes_lineage_artefacts(
        self,
        workspace: Path,
        run_id: str,
        audit_key: bytes,
    ) -> None:
        sdd_dir = workspace / ".sdd"
        base_time = datetime.now(tz=UTC)
        _seed_run(sdd_dir=sdd_dir, run_id=run_id, audit_key=audit_key, base_time=base_time)
        records = _seed_lineage(sdd_dir=sdd_dir, run_id=run_id, base_time=base_time, count=3)

        result = assemble_from_run(
            run_id=run_id,
            since=base_time - timedelta(minutes=1),
            until=base_time + timedelta(minutes=5),
            sdd_dir=sdd_dir,
            workdir=workspace,
            risk_class="limited",
            audit_key=audit_key,
        )

        assert result.bundle.archive_path is not None
        with zipfile.ZipFile(result.bundle.archive_path) as zf:
            catalog = json.loads(zf.read("data_catalog.json").decode("utf-8"))

        assert catalog["lineage_artefact_count"] == 3
        cataloged_paths = {entry["path"] for entry in catalog["lineage_artefacts"]}
        expected_paths = {r.output_artifact.path for r in records}
        assert cataloged_paths == expected_paths
        for entry in catalog["lineage_artefacts"]:
            assert entry["regulatory_class"] == "high-risk"
            assert entry["producer"]["run_id"] == run_id

    def test_clause_map_loaded_from_yaml(
        self,
        workspace: Path,
        run_id: str,
        audit_key: bytes,
    ) -> None:
        sdd_dir = workspace / ".sdd"
        base_time = datetime.now(tz=UTC)
        _seed_run(sdd_dir=sdd_dir, run_id=run_id, audit_key=audit_key, base_time=base_time)

        # Write a project-local clause map override so we know the
        # bundle picked up the file (not an in-code default).
        config_dir = workspace / "config"
        config_dir.mkdir()
        custom_map = config_dir / "eu_ai_act_clause_map.yaml"
        custom_map.write_text(
            """schema_version: 1
regulation: "Custom regulation"
article: 12
mappings:
  - clause: "12(custom)"
    requirement: "Custom requirement under test."
    subsystem:
      module: "src/bernstein/core/security/audit.py"
      role: "Custom role"
    evidence_artefact: "events.jsonl"
""",
            encoding="utf-8",
        )

        result = assemble_from_run(
            run_id=run_id,
            since=base_time - timedelta(minutes=1),
            until=base_time + timedelta(minutes=5),
            sdd_dir=sdd_dir,
            workdir=workspace,
            risk_class="limited",
            audit_key=audit_key,
        )
        assert result.bundle.archive_path is not None
        with zipfile.ZipFile(result.bundle.archive_path) as zf:
            clause_map = json.loads(zf.read("clause_map.json").decode("utf-8"))

        assert clause_map["regulation"] == "Custom regulation"
        clauses = [m["clause"] for m in clause_map["mappings"]]
        assert "12(custom)" in clauses

    def test_chain_break_aborts_bundle(
        self,
        workspace: Path,
        run_id: str,
        audit_key: bytes,
    ) -> None:
        sdd_dir = workspace / ".sdd"
        base_time = datetime.now(tz=UTC)
        _seed_run(sdd_dir=sdd_dir, run_id=run_id, audit_key=audit_key, base_time=base_time)

        # Tamper: rewrite the second event's payload after the HMAC was
        # computed. The chain should break on line 2.
        run_audit_path = sdd_dir / "runtime" / "audit" / f"{run_id}.audit.jsonl"
        lines = run_audit_path.read_text().splitlines()
        tampered = json.loads(lines[1])
        tampered["actor"] = "tampered-actor"
        lines[1] = json.dumps(tampered, sort_keys=True)
        run_audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(ChainBreakError, match="HMAC mismatch"):
            assemble_from_run(
                run_id=run_id,
                since=base_time - timedelta(minutes=1),
                until=base_time + timedelta(minutes=5),
                sdd_dir=sdd_dir,
                workdir=workspace,
                risk_class="limited",
                audit_key=audit_key,
            )

    def test_missing_run_chain_raises(
        self,
        workspace: Path,
        audit_key: bytes,
    ) -> None:
        sdd_dir = workspace / ".sdd"
        with pytest.raises(FileNotFoundError):
            assemble_from_run(
                run_id="never-existed",
                since=datetime.now(tz=UTC) - timedelta(minutes=1),
                until=datetime.now(tz=UTC) + timedelta(minutes=5),
                sdd_dir=sdd_dir,
                workdir=workspace,
                risk_class="limited",
                audit_key=audit_key,
            )

    def test_window_filters_events(
        self,
        workspace: Path,
        run_id: str,
        audit_key: bytes,
    ) -> None:
        sdd_dir = workspace / ".sdd"
        base_time = datetime.now(tz=UTC)
        _seed_run(
            sdd_dir=sdd_dir,
            run_id=run_id,
            audit_key=audit_key,
            base_time=base_time,
            event_count=5,
        )

        # Window covers everything - sanity check.
        full = assemble_from_run(
            run_id=run_id,
            since=base_time - timedelta(hours=1),
            until=base_time + timedelta(hours=1),
            sdd_dir=sdd_dir,
            workdir=workspace,
            risk_class="limited",
            audit_key=audit_key,
            write=False,
        )
        assert full.bundle.event_count == 5
        assert full.chain_event_count == 5

        # Past window - no events but chain still verifies.
        past = assemble_from_run(
            run_id=run_id,
            since=base_time - timedelta(days=10),
            until=base_time - timedelta(days=9),
            sdd_dir=sdd_dir,
            workdir=workspace,
            risk_class="limited",
            audit_key=audit_key,
            write=False,
        )
        assert past.bundle.event_count == 0
        assert past.chain_event_count == 5  # chain still walked top-to-bottom
