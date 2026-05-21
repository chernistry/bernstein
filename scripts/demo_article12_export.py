#!/usr/bin/env python3
"""End-to-end demo for the EU AI Act Article 12 evidence pack.

Boots a tiny in-process orchestrator surface, emits three high-risk-class
events through the real per-run HMAC chain helper, calls
:func:`assemble_from_run`, dumps the bundle to ``/tmp/article12-demo/``,
verifies it via :func:`verify_bundle`, and prints the manifest.

Usage:
    uv run python scripts/demo_article12_export.py
    uv run python scripts/demo_article12_export.py --output /tmp/my-demo

Exit codes:
    0 on success, 1 on bundle verification failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Allow running the script straight from the repo without installing.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bernstein.core.persistence.lineage import (  # noqa: E402
    AgentRef,
    ArtifactRef,
    LineageRecord,
    LineageWriter,
)
from bernstein.core.security.article12_bundle import (  # noqa: E402
    assemble_from_run,
    emit_run_audit_event,
    verify_bundle,
)


def _seed_lineage_record(
    sdd_dir: Path,
    run_id: str,
    *,
    output_path: str,
    sha256: str,
    regulatory_class: str,
    timestamp: float,
) -> None:
    """Append one lineage record to the run's WAL via the real writer."""
    writer = LineageWriter.for_run(run_id=run_id, sdd_dir=sdd_dir)
    record = LineageRecord(
        output_artifact=ArtifactRef(path=output_path, sha256=sha256),
        inputs=[ArtifactRef(path="prompts/system.md", sha256="0" * 64)],
        producer=AgentRef(agent_id="claude-code", run_id=run_id, tick_id="t-001"),
        prompt_sha="deadbeef" * 8,
        model="claude-sonnet-4.5",
        cost_usd=0.0125,
        tokens=1234,
        timestamp=timestamp,
        regulatory_class=regulatory_class,
    )
    writer.emit(record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="/tmp/article12-demo",
        help="Directory for the generated bundle (default: /tmp/article12-demo).",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep the temporary .sdd workspace for inspection (default: cleaned up).",
    )
    args = parser.parse_args()

    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    # 1. Boot a tiny "run" workspace under tmp. Avoids polluting the
    #    real .sdd/ on disk while still exercising the production
    #    LineageWriter and audit helpers end-to-end.
    workspace = Path(tempfile.mkdtemp(prefix="bernstein-article12-demo-"))
    sdd_dir = workspace / ".sdd"
    sdd_dir.mkdir(parents=True, exist_ok=True)
    audit_key = b"x" * 32  # demo-only key - production loads from XDG state
    run_id = f"demo-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"

    print(f"workspace : {workspace}")
    print(f"run_id    : {run_id}")

    # 2. Emit three real, HMAC-chained audit events through the same
    #    helper :mod:`assemble_from_run` reads from. Each event tags a
    #    high-risk-class action so the bundle's data catalog reflects
    #    the regulatory weight.
    base_time = datetime.now(tz=UTC)
    events_payload = [
        ("task.created", "alice", "task", "T-1001", {"role": "compliance"}),
        ("agent.spawned", "orchestrator", "agent", "A-001", {"task": "T-1001"}),
        ("task.completed", "alice", "task", "T-1001", {"status": "ok"}),
    ]
    for event_type, actor, resource_type, resource_id, details in events_payload:
        emit_run_audit_event(
            sdd_dir=sdd_dir,
            run_id=run_id,
            event_type=event_type,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            audit_key=audit_key,
        )

    # 3. Seed three lineage records so data_catalog.json picks them up.
    #    Each record carries `regulatory_class="high-risk"` to mirror an
    #    Article 6 high-risk classification.
    for idx in range(3):
        _seed_lineage_record(
            sdd_dir=sdd_dir,
            run_id=run_id,
            output_path=f"src/handler_{idx}.py",
            sha256=f"{idx:064d}",
            regulatory_class="high-risk",
            timestamp=base_time.timestamp() + idx,
        )

    # 4. Assemble the bundle from the run.
    since = base_time - timedelta(minutes=1)
    until = base_time + timedelta(minutes=5)
    result = assemble_from_run(
        run_id=run_id,
        since=since,
        until=until,
        sdd_dir=sdd_dir,
        workdir=ROOT,
        risk_class="high",
        audit_key=audit_key,
        output_dir=output_root,
    )
    bundle = result.bundle
    print(f"\nbundle id           : {bundle.bundle_id}")
    print(f"window              : {bundle.since} → {bundle.until}")
    print(f"event_count         : {bundle.event_count}")
    print(f"chain_anchor        : {bundle.chain_anchor[:24]}…")
    print(f"retention_until     : {bundle.retention.retention_until}")
    print(f"sha256              : {bundle.sha256}")
    print(f"archive_path        : {bundle.archive_path}")
    print(f"chain_event_count   : {result.chain_event_count}")
    print(f"catalog_artefacts   : {result.catalog_artefact_count}")

    # 5. Verify it.
    if bundle.archive_path is None:
        print("\nERROR: bundle did not produce an archive path", file=sys.stderr)
        return 1
    verification = verify_bundle(bundle.archive_path)
    if not verification.ok:
        print("\nERROR: bundle verification FAILED", file=sys.stderr)
        for err in verification.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\n--- manifest ---")
    print(json.dumps(verification.manifest, indent=2, sort_keys=True))

    print(f"\nVERIFIED. Bundle at: {bundle.archive_path}")

    if not args.keep_workspace:
        shutil.rmtree(workspace, ignore_errors=True)
    else:
        print(f"workspace kept at: {workspace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
