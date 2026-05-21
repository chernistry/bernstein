"""End-to-end fixture test for the EU AI Act Article 12 evidence pack.

Builds a synthetic HMAC-chained audit log, generates a bundle through
:func:`build_article12_bundle`, then verifies:

* the bundle's manifest hashes match the on-disk artefact contents
* the embedded ``events.jsonl`` HMAC chain validates against the original
  ``AuditLog`` key
* :func:`compute_retention_pin` produces correct horizons for each risk
  class (Article 12(3) - 6-month minimum, 10-year for high-risk)
* a second build of the same window is byte-identical (deterministic
  export - required for spot-audit reproducibility)
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bernstein.core.security.article12_bundle import (
    BUNDLE_SCHEMA_VERSION,
    HIGH_RISK_RETENTION_YEARS,
    MINIMUM_RETENTION_DAYS,
    build_article12_bundle,
    compute_retention_pin,
    validate_retention,
    verify_bundle,
)
from bernstein.core.security.audit import AuditLog


def _seed_audit_log(audit_dir: Path) -> AuditLog:
    """Populate ``audit_dir`` with three HMAC-chained events.

    Args:
        audit_dir: Directory to write the daily JSONL log into.

    Returns:
        The :class:`AuditLog` used to write the events (still tied to the
        same key file via the default key-loader).
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    log = AuditLog(audit_dir, key=b"x" * 32)
    log.log("task.created", "alice", "task", "T-1", {"role": "backend"})
    log.log("agent.spawned", "orchestrator", "agent", "A-1", {"task": "T-1"})
    log.log("task.completed", "alice", "task", "T-1", {"status": "ok"})
    return log


# ---------------------------------------------------------------------------
# Retention helper
# ---------------------------------------------------------------------------


class TestRetentionPin:
    """Article 12(3) retention math."""

    def test_high_risk_pins_ten_years(self) -> None:
        last_event_ts = "2026-08-01T00:00:00+00:00"
        pin = compute_retention_pin("high", last_event_ts)
        assert pin.risk_class == "high"
        # 10 * 365.25 = 3652.5 → 3653 (round-half-to-even or up).
        assert pin.retention_days >= HIGH_RISK_RETENTION_YEARS * 365
        assert pin.retention_until.startswith("2036-")

    def test_limited_pins_six_months(self) -> None:
        pin = compute_retention_pin("limited", "2026-08-01T00:00:00+00:00")
        assert pin.retention_days == MINIMUM_RETENTION_DAYS

    def test_validate_rejects_below_floor(self) -> None:
        last_event_ts = "2026-08-01T00:00:00+00:00"
        bad = compute_retention_pin("limited", last_event_ts)
        # Manually shrink retention_days to simulate tampering.
        from dataclasses import replace

        shrunk = replace(bad, retention_days=30)
        ok, reason = validate_retention(
            shrunk,
            now=datetime(2026, 8, 2, tzinfo=UTC),
        )
        assert not ok
        assert "below Article 12(3) floor" in reason

    def test_validate_rejects_after_horizon(self) -> None:
        pin = compute_retention_pin("limited", "2020-01-01T00:00:00+00:00")
        ok, reason = validate_retention(pin, now=datetime(2026, 8, 1, tzinfo=UTC))
        assert not ok
        assert "horizon" in reason

    def test_validate_passes_within_window(self) -> None:
        pin = compute_retention_pin("high", "2026-08-01T00:00:00+00:00")
        ok, reason = validate_retention(pin, now=datetime(2027, 1, 1, tzinfo=UTC))
        assert ok, reason


# ---------------------------------------------------------------------------
# Bundle assembler
# ---------------------------------------------------------------------------


class TestArticle12BundleE2E:
    """Generate a tiny audit chain → export → verify."""

    def test_build_then_verify(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / ".sdd" / "audit"
        log = _seed_audit_log(audit_dir)
        # Sanity: the chain we just produced must validate.
        ok, errors = log.verify()
        assert ok, errors

        # Window covers all of today (UTC).
        today = datetime.now(tz=UTC).date()
        since = f"{today.isoformat()}T00:00:00+00:00"
        until = f"{(today + timedelta(days=1)).isoformat()}T00:00:00+00:00"

        output_dir = tmp_path / ".sdd" / "evidence"
        bundle = build_article12_bundle(
            audit_dir=audit_dir,
            since=since,
            until=until,
            risk_class="high",
            output_dir=output_dir,
            write=True,
        )

        assert bundle.event_count == 3
        assert bundle.archive_path is not None
        assert bundle.archive_path.exists()
        assert bundle.risk_class == "high"
        assert bundle.chain_anchor != "0" * 64
        # Retention horizon must be pinned ~10 years out.
        expected_year_prefix = f"{today.year + HIGH_RISK_RETENTION_YEARS}"
        assert bundle.retention.retention_until.startswith(expected_year_prefix)

        # Verifier must approve the produced bundle.
        result = verify_bundle(bundle.archive_path)
        assert result.ok, result.errors
        assert result.manifest["schema_version"] == BUNDLE_SCHEMA_VERSION
        assert result.manifest["event_count"] == 3
        assert result.manifest["chain_anchor"] == bundle.chain_anchor

        # The events.jsonl extracted from the bundle must verify under
        # the original AuditLog key - we replay it through a fresh
        # AuditLog rooted at a tmp dir that holds only the slice.
        replay_dir = tmp_path / "replay"
        replay_dir.mkdir()
        with zipfile.ZipFile(bundle.archive_path) as zf:
            (replay_dir / f"{today.isoformat()}.jsonl").write_bytes(zf.read("events.jsonl"))
        replay_log = AuditLog(replay_dir, key=b"x" * 32)
        replay_ok, replay_errors = replay_log.verify()
        assert replay_ok, replay_errors

    def test_deterministic_byte_identical_rebuild(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / ".sdd" / "audit"
        _seed_audit_log(audit_dir)
        today = datetime.now(tz=UTC).date()
        since = f"{today.isoformat()}T00:00:00+00:00"
        until = f"{(today + timedelta(days=1)).isoformat()}T00:00:00+00:00"

        first = build_article12_bundle(
            audit_dir=audit_dir,
            since=since,
            until=until,
            risk_class="limited",
            output_dir=tmp_path / "out1",
            write=True,
        )
        second = build_article12_bundle(
            audit_dir=audit_dir,
            since=since,
            until=until,
            risk_class="limited",
            output_dir=tmp_path / "out2",
            write=True,
        )

        assert first.bundle_id == second.bundle_id
        assert first.sha256 == second.sha256
        assert first.archive_path is not None
        assert second.archive_path is not None
        assert (
            hashlib.sha256(first.archive_path.read_bytes()).hexdigest()
            == hashlib.sha256(second.archive_path.read_bytes()).hexdigest()
        )

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / ".sdd" / "audit"
        _seed_audit_log(audit_dir)
        today = datetime.now(tz=UTC).date()
        since = f"{today.isoformat()}T00:00:00+00:00"
        until = f"{(today + timedelta(days=1)).isoformat()}T00:00:00+00:00"
        bundle = build_article12_bundle(
            audit_dir=audit_dir,
            since=since,
            until=until,
            risk_class="minimal",
            output_dir=tmp_path / "out",
            write=False,
        )
        assert bundle.archive_path is None
        assert bundle.event_count == 3
        assert bundle.sha256  # still computed from the in-memory bytes

    def test_empty_window_safe(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / ".sdd" / "audit"
        audit_dir.mkdir(parents=True)
        bundle = build_article12_bundle(
            audit_dir=audit_dir,
            since="2026-08-01T00:00:00+00:00",
            until="2026-08-02T00:00:00+00:00",
            risk_class="limited",
            output_dir=tmp_path / "out",
            write=True,
        )
        assert bundle.event_count == 0
        assert bundle.chain_anchor == "0" * 64
        assert bundle.archive_path is not None
        # Retention pin uses ``since`` as last-event proxy when window is empty.
        assert bundle.retention.last_event_ts == "2026-08-01T00:00:00+00:00"
