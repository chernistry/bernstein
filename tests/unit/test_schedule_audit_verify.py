"""Unit tests for ``schedule audit`` projection + chain verification (#1838).

``bernstein schedule audit`` must *re-derive* each fire's projection from
its persisted inputs and compare the recomputed ``projection_hash`` to the
recorded one, and cross-check the receipt against the matching
``schedule.fire`` entry in the HMAC audit chain. A receipt whose
``projection_hash`` was edited to a self-consistent but wrong value, or
that disagrees with the chain, must be reported as a hard failure - not
printed as if intact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bernstein.core.orchestration.schedule_supervisor import (
    AUDIT_EVENT_TYPE,
    ScheduleSupervisor,
    load_receipts,
    verify_receipts,
)
from bernstein.core.planning.schedule_store import ScheduleStore
from bernstein.core.security.audit import AuditLog


def _audit_log_with_shared_key(sdd_dir: Path) -> AuditLog:
    """Return an AuditLog rooted at ``<sdd>/audit`` (matches production).

    ``bernstein schedule run`` wires the supervisor's audit log at
    ``.sdd/audit`` and ``verify_receipts`` reads from the same path, so
    the test must place the chain there for the chain cross-check to run.
    """
    audit_dir = sdd_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    key_path = sdd_dir / "audit.key"
    key_path.write_bytes(b"deterministic-schedule-key-32-bytes")
    key_path.chmod(0o600)
    return AuditLog(audit_dir=audit_dir, key_path=key_path)


def _seed_and_run(sdd_dir: Path) -> str:
    """Seed a schedule, run a deterministic burst, return the schedule id."""
    store = ScheduleStore(sdd_dir)
    schedule = store.add(cron="* * * * *", goal="Send nightly digest", misfire_policy="catch_up")
    last_fire = int(datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp())
    store.update_last_fire(schedule.id, float(last_fire))
    audit = _audit_log_with_shared_key(sdd_dir)
    dispatched: list[Any] = []
    sup = ScheduleSupervisor(store, dispatched.append, audit, catch_up_limit=10)
    now = int(datetime(2030, 1, 1, 12, 5, 30, tzinfo=UTC).timestamp())
    sup.tick(now=now)
    return schedule.id


def _receipt_files(sdd_dir: Path) -> list[Path]:
    return sorted((sdd_dir / "runtime" / "schedule_receipts").glob("*.json"))


def _audit_files(sdd_dir: Path) -> list[Path]:
    return sorted((sdd_dir / "audit").glob("*.jsonl"))


class TestVerifyHonestReceipts:
    def test_all_dispatched_receipts_verified(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / "sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)

        report = verify_receipts(sdd_dir)
        assert report.ok, report.failures
        dispatched = [r for r in report.results if not r.counterfactual]
        assert dispatched, "expected at least one dispatched fire"
        for r in dispatched:
            assert r.verified
            assert r.projection_match
            assert r.chain_match
            # The recomputed hash must equal the recorded one byte-for-byte.
            assert r.recomputed_projection_hash == r.recorded_projection_hash

    def test_counterfactual_receipts_skipped_not_flagged(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / "sdd"
        sdd_dir.mkdir(parents=True)
        # A skip-policy schedule with many missed windows emits a
        # counterfactual receipt carrying empty hashes by design.
        store = ScheduleStore(sdd_dir)
        schedule = store.add(cron="* * * * *", goal="Skip probe", misfire_policy="skip")
        last_fire = int(datetime(2030, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp())
        store.update_last_fire(schedule.id, float(last_fire))
        audit = _audit_log_with_shared_key(sdd_dir)
        sup = ScheduleSupervisor(store, lambda _e: None, audit, catch_up_limit=10)
        now = int(datetime(2030, 1, 1, 12, 10, 30, tzinfo=UTC).timestamp())
        sup.tick(now=now)

        report = verify_receipts(sdd_dir)
        counterfactuals = [r for r in report.results if r.counterfactual]
        assert counterfactuals, "expected a counterfactual receipt"
        for r in counterfactuals:
            assert r.skipped
            assert not r.mismatch
        # Counterfactuals must not make the overall audit fail.
        assert report.ok, report.failures


class TestVerifyDetectsProjectionTamper:
    def test_edited_receipt_projection_hash_fails(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / "sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)

        # Pick a dispatched receipt and edit its projection_hash to a
        # plausible-but-wrong value, leaving the file well-formed.
        target = None
        for path in _receipt_files(sdd_dir):
            data = json.loads(path.read_text())
            if data.get("dispatched") and data.get("projection_hash"):
                target = path
                break
        assert target is not None
        data = json.loads(target.read_text())
        data["projection_hash"] = "0" * 64
        target.write_text(json.dumps(data, sort_keys=True, indent=2))

        report = verify_receipts(sdd_dir)
        assert not report.ok
        # The offending receipt must be named in the failures.
        assert any(target.stem.rsplit("-", 1)[0] in f or "0000000000000000" in f for f in report.failures)
        bad = [r for r in report.results if r.recorded_projection_hash == "0" * 64]
        assert bad and bad[0].mismatch and not bad[0].projection_match


class TestVerifyDetectsChainMismatch:
    def test_chain_entry_projection_hash_disagrees_with_receipt(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / "sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)

        # Mutate the schedule.fire chain entry's projection_hash, leaving
        # the receipt intact. The receipt still self-verifies against the
        # projection, but it now disagrees with the chain.
        audit_file = _audit_files(sdd_dir)[0]
        lines = audit_file.read_text().splitlines()
        new_lines = []
        mutated = False
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)
            if not mutated and entry.get("event_type") == AUDIT_EVENT_TYPE:
                entry["details"]["projection_hash"] = "f" * 64
                mutated = True
            new_lines.append(json.dumps(entry, sort_keys=True))
        assert mutated
        audit_file.write_text("\n".join(new_lines) + "\n")

        report = verify_receipts(sdd_dir)
        assert not report.ok
        assert any(r.mismatch and not r.chain_match for r in report.results)


class TestVerifyDetectsLinkageBreak:
    def test_broken_prev_to_chain_linkage_reported(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / "sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)

        # Break the receipt-to-receipt linkage: rewrite the second
        # dispatched receipt's prev_chain_digest so it no longer points at
        # the first receipt's chain_digest.
        dispatched = [p for p in _receipt_files(sdd_dir) if json.loads(p.read_text()).get("dispatched")]
        assert len(dispatched) >= 2
        second = dispatched[1]
        data = json.loads(second.read_text())
        data["prev_chain_digest"] = "deadbeef" * 8
        second.write_text(json.dumps(data, sort_keys=True, indent=2))

        report = verify_receipts(sdd_dir)
        assert not report.ok
        assert any("linkage" in f.lower() for f in report.failures)


class TestReDerivationIsByteIdentical:
    def test_recomputed_matches_recorded_for_every_honest_fire(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / "sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)

        receipts = [r for r in load_receipts(sdd_dir) if r.dispatched]
        report = verify_receipts(sdd_dir)
        by_key = {(r.schedule_id, r.fire_time): r for r in report.results}
        for rec in receipts:
            res = by_key[(rec.schedule_id, rec.fire_time)]
            assert res.recomputed_projection_hash == rec.projection_hash


class TestScheduleAuditCommand:
    """The CLI verb must exit non-zero on tamper, 0 on honest records."""

    def _invoke(self, sdd_dir: Path, as_json: bool = False) -> Any:
        from click.testing import CliRunner

        from bernstein.cli.commands.schedule_cmd import schedule_audit

        runner = CliRunner()
        # The command resolves .sdd relative to cwd.
        import os

        cwd = os.getcwd()
        try:
            os.chdir(sdd_dir.parent)
            args = ["--json"] if as_json else []
            return runner.invoke(schedule_audit, args)
        finally:
            os.chdir(cwd)

    def test_exit_zero_when_intact(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / ".sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)
        result = self._invoke(sdd_dir)
        assert result.exit_code == 0, result.output

    def test_exit_nonzero_when_projection_tampered(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / ".sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)
        # Tamper a dispatched receipt.
        for path in _receipt_files(sdd_dir):
            data = json.loads(path.read_text())
            if data.get("dispatched") and data.get("projection_hash"):
                data["projection_hash"] = "0" * 64
                path.write_text(json.dumps(data, sort_keys=True, indent=2))
                break
        result = self._invoke(sdd_dir)
        assert result.exit_code != 0, result.output

    def test_json_marks_each_receipt_verified(self, tmp_path: Path) -> None:
        sdd_dir = tmp_path / ".sdd"
        sdd_dir.mkdir(parents=True)
        _seed_and_run(sdd_dir)
        result = self._invoke(sdd_dir, as_json=True)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["receipts"]
        dispatched = [r for r in payload["receipts"] if not r.get("counterfactual")]
        assert dispatched
        for r in dispatched:
            assert r["verified"] is True
