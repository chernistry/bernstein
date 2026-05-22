import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bernstein.core.audit import (
    _GENESIS_HMAC,  # pyright: ignore[reportPrivateUsage]
    ArchiveResult,
    AuditLog,
    RetentionPolicy,
)


def test_audit_log_record(tmp_path: Path) -> None:
    """Test recording a single audit event."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")

    event = log.log("task.created", "system", "task", "task-1", {"foo": "bar"})

    assert event.event_type == "task.created"
    assert event.actor == "system"
    assert event.resource_id == "task-1"
    assert event.details == {"foo": "bar"}
    assert event.prev_hmac == _GENESIS_HMAC
    assert len(event.hmac) == 64


def test_audit_log_persistence(tmp_path: Path) -> None:
    """Test that audit log state is persisted and recoverable."""
    audit_dir = tmp_path / "audit"
    key = b"test-key"
    log1 = AuditLog(audit_dir, key=key)
    event1 = log1.log("type1", "actor1", "res", "id1")

    # Reload log from same directory with same key
    log2 = AuditLog(audit_dir, key=key)
    assert log2._prev_hmac == event1.hmac  # pyright: ignore[reportPrivateUsage]

    events = log2.query()
    assert len(events) == 1
    assert events[0].event_type == "type1"
    assert events[0].hmac == event1.hmac


def test_audit_log_chaining(tmp_path: Path) -> None:
    """Test that events are chained via HMAC."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")

    event1 = log.log("e1", "a1", "r1", "i1")
    event2 = log.log("e2", "a2", "r2", "i2")

    assert event2.prev_hmac == event1.hmac
    assert event2.hmac != event1.hmac


def test_audit_log_hash_validation(tmp_path: Path) -> None:
    """Test verifying an intact audit log."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")
    log.log("e1", "a1", "r1", "i1")
    log.log("e2", "a2", "r2", "i2")

    valid, errors = log.verify()
    assert valid is True
    assert not errors


def test_audit_log_integrity_check_tamper_payload(tmp_path: Path) -> None:
    """Test that tampering with event payload is detected."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")
    log.log("e1", "a1", "r1", "i1")

    # Tamper with the log file content (change actor)
    log_files = list(audit_dir.glob("*.jsonl"))
    content = log_files[0].read_text()
    tampered_content = content.replace('"a1"', '"tampered"')
    log_files[0].write_text(tampered_content)

    valid, errors = log.verify()
    assert valid is False
    assert any("HMAC mismatch" in err for err in errors)


def test_audit_log_integrity_check_broken_chain(tmp_path: Path) -> None:
    """Test that breaking the HMAC chain is detected."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")
    log.log("e1", "a1", "r1", "i1")
    log.log("e2", "a2", "r2", "i2")

    # Tamper with prev_hmac in second event record
    log_files = list(audit_dir.glob("*.jsonl"))
    lines = log_files[0].read_text().splitlines()
    data = json.loads(lines[1])
    data["prev_hmac"] = "0" * 64  # Incorrect prev_hmac
    lines[1] = json.dumps(data, sort_keys=True)
    log_files[0].write_text("\n".join(lines) + "\n")

    valid, errors = log.verify()
    assert valid is False
    assert any("prev_hmac mismatch" in err for err in errors)


def test_audit_log_query_filters(tmp_path: Path) -> None:
    """Test querying audit events with filters."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")
    log.log("type.A", "actor.1", "res", "id1")
    log.log("type.B", "actor.1", "res", "id2")
    log.log("type.A", "actor.2", "res", "id3")

    # Filter by type
    assert len(log.query(event_type="type.A")) == 2

    # Filter by actor
    assert len(log.query(actor="actor.1")) == 2

    # Filter by both
    results = log.query(event_type="type.A", actor="actor.1")
    assert len(results) == 1
    assert results[0].resource_id == "id1"


# -- retention & archive tests -----------------------------------------


def _create_old_log(audit_dir: Path, days_ago: int, key: bytes = b"test-key") -> str:
    """Write a dummy JSONL log file dated ``days_ago`` days in the past."""
    date = (datetime.now(tz=UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    log_path = audit_dir / f"{date}.jsonl"
    entry = {
        "timestamp": f"{date}T00:00:00.000000Z",
        "event_type": "test",
        "actor": "test",
        "resource_type": "test",
        "resource_id": "id1",
        "details": {},
        "prev_hmac": _GENESIS_HMAC,
        "hmac": "a" * 64,
    }
    log_path.write_text(json.dumps(entry, sort_keys=True) + "\n")
    return log_path.name


def test_archive_compresses_old_logs(tmp_path: Path) -> None:
    """Logs older than retention_days are gzip-compressed and removed."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    old_name = _create_old_log(audit_dir, days_ago=100)

    log = AuditLog(audit_dir, key=b"test-key")
    result = log.archive(RetentionPolicy(retention_days=90))

    assert old_name in result.archived
    assert not (audit_dir / old_name).exists()
    gz = audit_dir / "archive" / f"{old_name}.gz"
    assert gz.exists()
    # Verify the gzip content is valid JSONL
    content = gzip.decompress(gz.read_bytes()).decode()
    entry = json.loads(content.strip())
    assert entry["event_type"] == "test"


def test_archive_skips_recent_logs(tmp_path: Path) -> None:
    """Logs within the retention window are not archived."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    recent_name = _create_old_log(audit_dir, days_ago=10)

    log = AuditLog(audit_dir, key=b"test-key")
    result = log.archive(RetentionPolicy(retention_days=90))

    assert recent_name in result.skipped
    assert not result.archived
    assert (audit_dir / recent_name).exists()


def test_archive_skips_already_archived(tmp_path: Path) -> None:
    """If a .gz already exists in the archive dir, skip the file."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    old_name = _create_old_log(audit_dir, days_ago=100)

    archive_dir = audit_dir / "archive"
    archive_dir.mkdir()
    # Pre-create the gz file
    (archive_dir / f"{old_name}.gz").write_bytes(b"existing")

    log = AuditLog(audit_dir, key=b"test-key")
    result = log.archive(RetentionPolicy(retention_days=90))

    assert old_name in result.skipped
    assert not result.archived


def test_archive_default_policy(tmp_path: Path) -> None:
    """Default retention policy uses 90 days."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    _create_old_log(audit_dir, days_ago=91)
    recent_name = _create_old_log(audit_dir, days_ago=30)

    log = AuditLog(audit_dir, key=b"test-key")
    result = log.archive()

    assert len(result.archived) == 1
    assert recent_name in result.skipped


def test_archive_custom_subdir(tmp_path: Path) -> None:
    """Archive subdirectory is configurable."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    _create_old_log(audit_dir, days_ago=200)

    log = AuditLog(audit_dir, key=b"test-key")
    policy = RetentionPolicy(retention_days=90, archive_subdir="old_logs")
    result = log.archive(policy)

    assert len(result.archived) == 1
    assert (audit_dir / "old_logs").is_dir()
    assert "old_logs" in result.archive_dir


def test_archive_result_dataclass(tmp_path: Path) -> None:
    """ArchiveResult is a proper frozen dataclass."""
    r = ArchiveResult(archived=["a.jsonl"], archive_dir="/tmp/x", skipped=["b.jsonl"])
    assert r.archived == ["a.jsonl"]
    assert r.archive_dir == "/tmp/x"
    assert r.skipped == ["b.jsonl"]


def test_retention_policy_defaults() -> None:
    """RetentionPolicy has sensible defaults."""
    p = RetentionPolicy()
    assert p.retention_days == 90
    assert p.archive_subdir == "archive"


# -- archive-boundary chain verification (issue #1835) -----------------


def _single_live_log(audit_dir: Path) -> Path:
    """Return the sole live ``*.jsonl`` file in ``audit_dir``.

    The chain-builder helpers append all events in one shot, so exactly one
    daily file exists; resolving it (instead of recomputing today's date)
    keeps date derivation immune to a UTC midnight roll-over mid-helper.
    """
    live = sorted(audit_dir.glob("*.jsonl"))
    assert len(live) == 1, f"expected exactly one live log, found {[p.name for p in live]}"
    return live[0]


def _build_two_day_chain(audit_dir: Path, key: bytes = b"test-key") -> tuple[str, str]:
    """Build a genuine HMAC chain split across two dated JSONL files.

    Events are produced via the real :meth:`AuditLog.log` API so the chain
    is byte-identical to production output, then the first events are moved
    into a file dated one day in the past while the remainder stay in
    today's file. The result is a single continuous chain whose link
    crosses the ``<yesterday> -> <today>`` file boundary, which is exactly
    the boundary :meth:`AuditLog.archive` later compresses.

    Returns:
        ``(old_name, today_name)`` - the two dated filenames written.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    log = AuditLog(audit_dir, key=key)
    log.log("e1", "a1", "r1", "i1")
    log.log("e2", "a2", "r2", "i2")
    log.log("e3", "a3", "r3", "i3")

    # Anchor every derived date on the file ``log`` actually wrote rather than
    # a fresh ``datetime.now`` call: a UTC midnight roll-over between the log
    # calls and date derivation would otherwise drift the filenames and flake
    # the test. There is exactly one live file at this point.
    today_path = _single_live_log(audit_dir)
    today_date = datetime.strptime(today_path.stem, "%Y-%m-%d").replace(tzinfo=UTC)
    lines = today_path.read_text().splitlines()
    assert len(lines) == 3

    yesterday = (today_date - timedelta(days=1)).strftime("%Y-%m-%d")
    old_path = audit_dir / f"{yesterday}.jsonl"
    # First two events live in the older (archivable) file; the third
    # event - whose prev_hmac links back into the older file - stays live.
    old_path.write_text("\n".join(lines[:2]) + "\n")
    today_path.write_text(lines[2] + "\n")
    return old_path.name, today_path.name


def _build_three_day_chain(audit_dir: Path, key: bytes = b"test-key") -> tuple[str, str, str]:
    """Build a genuine HMAC chain split across three dated JSONL files.

    Like :func:`_build_two_day_chain` but produces two archivable older days
    plus today, so a test can archive *both* older days and then damage one
    archived segment while a *later* archived segment still exists - the
    linkage break is then only observable by a verifier that actually reads
    archived segments in order (issue #1835).

    Returns:
        ``(day1_name, day2_name, today_name)`` oldest-first.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    log = AuditLog(audit_dir, key=key)
    for i in range(1, 6):
        log.log(f"e{i}", f"a{i}", f"r{i}", f"i{i}")

    # Anchor on the file ``log`` wrote (see ``_build_two_day_chain``) so a
    # midnight roll-over cannot drift the derived dates.
    today_path = _single_live_log(audit_dir)
    today_date = datetime.strptime(today_path.stem, "%Y-%m-%d").replace(tzinfo=UTC)
    lines = today_path.read_text().splitlines()
    assert len(lines) == 5

    day1 = (today_date - timedelta(days=2)).strftime("%Y-%m-%d")
    day2 = (today_date - timedelta(days=1)).strftime("%Y-%m-%d")
    day1_path = audit_dir / f"{day1}.jsonl"
    day2_path = audit_dir / f"{day2}.jsonl"
    day1_path.write_text("\n".join(lines[:2]) + "\n")
    day2_path.write_text("\n".join(lines[2:4]) + "\n")
    today_path.write_text(lines[4] + "\n")
    return day1_path.name, day2_path.name, today_path.name


def test_verify_passes_across_archive_boundary(tmp_path: Path) -> None:
    """An intact chain still verifies after the older day is archived.

    Reproduces issue #1835: ``archive`` gzips and unlinks the older
    ``.jsonl`` while ``verify`` only globs live ``*.jsonl`` and re-seeds from
    genesis, so the first surviving entry's ``prev_hmac`` (which points into
    the archived file) wrongly reports a mismatch.
    """
    audit_dir = tmp_path / "audit"
    old_name, _today = _build_two_day_chain(audit_dir)

    # Sanity: the un-archived split chain is valid end to end.
    pre_valid, pre_errors = AuditLog(audit_dir, key=b"test-key").verify()
    assert pre_valid is True, pre_errors

    result = AuditLog(audit_dir, key=b"test-key").archive(RetentionPolicy(retention_days=0))
    assert old_name in result.archived
    assert not (audit_dir / old_name).exists()
    assert (audit_dir / "archive" / f"{old_name}.gz").exists()

    # The reopened log must verify across the archive boundary.
    valid, errors = AuditLog(audit_dir, key=b"test-key").verify()
    assert valid is True, errors
    assert errors == []


def test_verify_detects_tamper_inside_archived_segment(tmp_path: Path) -> None:
    """Flipping a byte inside an archived .gz surfaces as an HMAC error."""
    audit_dir = tmp_path / "audit"
    old_name, _today = _build_two_day_chain(audit_dir)
    AuditLog(audit_dir, key=b"test-key").archive(RetentionPolicy(retention_days=0))

    gz_path = audit_dir / "archive" / f"{old_name}.gz"
    payload = gzip.decompress(gz_path.read_bytes()).decode()
    tampered = payload.replace('"a1"', '"tampered"')
    assert tampered != payload
    gz_path.write_bytes(gzip.compress(tampered.encode()))

    valid, errors = AuditLog(audit_dir, key=b"test-key").verify()
    assert valid is False
    assert any("HMAC mismatch" in err for err in errors)
    # The error must name the offending archived segment, not a live file.
    assert any(gz_path.name in err for err in errors)


def test_verify_detects_deleted_archived_segment(tmp_path: Path) -> None:
    """Deleting an interior archived .gz surfaces as a linkage break.

    Two older days are archived; the *earlier* archived segment is then
    removed (as an operator pruning ``archive/`` to save space would).  The
    surviving later archived segment's ``prev_hmac`` points into the deleted
    one, so the chain must report a linkage break naming that segment - a
    break only a verifier that reads archived segments can detect.
    """
    audit_dir = tmp_path / "audit"
    day1_name, day2_name, _today = _build_three_day_chain(audit_dir)
    result = AuditLog(audit_dir, key=b"test-key").archive(RetentionPolicy(retention_days=0))
    assert day1_name in result.archived
    assert day2_name in result.archived

    (audit_dir / "archive" / f"{day1_name}.gz").unlink()
    surviving = f"{day2_name}.gz"

    valid, errors = AuditLog(audit_dir, key=b"test-key").verify()
    assert valid is False
    assert any("prev_hmac mismatch" in err for err in errors)
    # The break must be attributed to the now-orphaned surviving segment.
    assert any(surviving in err and "prev_hmac mismatch" in err for err in errors)


def test_verify_no_archive_unchanged(tmp_path: Path) -> None:
    """A chain with zero archived segments verifies exactly as before."""
    audit_dir = tmp_path / "audit"
    log = AuditLog(audit_dir, key=b"test-key")
    log.log("e1", "a1", "r1", "i1")
    log.log("e2", "a2", "r2", "i2")

    valid, errors = AuditLog(audit_dir, key=b"test-key").verify()
    assert valid is True
    assert errors == []


def test_recover_chain_tail_stable_across_archive(tmp_path: Path) -> None:
    """The recovered tip is identical whether or not old days are archived.

    Guards the writer path: a process reopening an archived log must resume
    from the true chain tip so a freshly appended event does not fork the
    chain back to genesis.
    """
    audit_dir = tmp_path / "audit"
    _build_two_day_chain(audit_dir)

    tip_before = AuditLog(audit_dir, key=b"test-key")._prev_hmac  # pyright: ignore[reportPrivateUsage]

    AuditLog(audit_dir, key=b"test-key").archive(RetentionPolicy(retention_days=0))

    tip_after = AuditLog(audit_dir, key=b"test-key")._prev_hmac  # pyright: ignore[reportPrivateUsage]
    assert tip_after == tip_before

    # Appending after reopening an archived log keeps the chain intact.
    reopened = AuditLog(audit_dir, key=b"test-key")
    reopened.log("e4", "a4", "r4", "i4")
    valid, errors = AuditLog(audit_dir, key=b"test-key").verify()
    assert valid is True, errors
