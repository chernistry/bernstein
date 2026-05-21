"""Tests for ``bernstein audit archive`` CLI command.

The archive command is the operator-side "option B" fix for the
2026-05-13 bughunt ticket: when the audit HMAC key has been rotated
without rehashing old entries, ``bernstein doctor airgap`` flags the
chain as broken. Rather than ask the operator to manually mv files
out of ``.sdd/audit/``, this command moves them to an out-of-chain
archive directory and writes per-file metadata so the move is
auditable and reversible.

Tests cover:
  * dry-run prints plan and moves nothing
  * --corrupt archives only the corrupt files
  * --before <date> archives only the date-filtered subset
  * idempotency: refuses to overwrite an already-archived file
  * post-archive verify is reported
  * --yes is required for a real move (default refuses)
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

from bernstein.cli.commands.audit_cmd import audit_group
from bernstein.core.security.audit import AUDIT_KEY_ENV, AuditLog

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated audit dir + a tmp HMAC key file.

    Pinning ``BERNSTEIN_AUDIT_KEY_PATH`` at a tmp path keeps every test
    away from the operator's real audit key under ``~/.local/state/``.
    The same key seeds both the AuditLog instances used to build the
    fixture log AND the archive command's verify pass - without this
    matching, the post-archive verify would always fail.
    """
    audit_dir = tmp_path / ".sdd" / "audit"
    audit_dir.mkdir(parents=True)

    key_path = tmp_path / "audit.key"
    key_bytes = b"a" * 64  # 32 hex bytes worth, deterministic for tests
    key_path.write_bytes(key_bytes)
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 required by loader

    monkeypatch.setenv(AUDIT_KEY_ENV, str(key_path))
    return audit_dir


def _seed_clean_log(
    audit_dir: Path,
    day: str,
    count: int,
    *,
    chain_from_genesis: bool = False,
) -> list[str]:
    """Seed ``audit_dir/<day>.jsonl`` with HMAC-correct chained entries.

    The AuditLog writer always writes to today's filename - we need a
    deterministic filename (so ``--before`` can target it), so we
    build entries by hand using the public canonical-form HMAC math
    from ``bernstein.core.security.audit``.

    Args:
        audit_dir: where to write the file
        day: filename stem (``YYYY-MM-DD``)
        count: how many entries to write
        chain_from_genesis: if True, ignore any earlier files in the
            audit dir and start the chain at the genesis HMAC. Useful
            when the earlier files are intentionally-broken (so this
            file's chain remains valid after they're archived out).
    """
    from bernstein.core.security.audit import _compute_hmac, load_or_create_audit_key

    key = load_or_create_audit_key()
    hmacs: list[str] = []
    prev = "0" * 64
    log_path = audit_dir / f"{day}.jsonl"

    if not chain_from_genesis:
        # Find any existing chain tail across other files in audit_dir so
        # this file slots in correctly. We sort ascending so any
        # alphabetically-earlier file is treated as upstream.
        earlier = sorted(p for p in audit_dir.glob("*.jsonl") if p.name < log_path.name)
        if earlier:
            last = earlier[-1].read_text().strip().splitlines()
            if last:
                prev = json.loads(last[-1])["hmac"]

    with log_path.open("a", encoding="utf-8", newline="") as fh:
        for i in range(count):
            entry = {
                "timestamp": f"{day}T00:00:{i:02d}.000000Z",
                "event_type": "test.evt",
                "actor": "test",
                "resource_type": "task",
                "resource_id": f"id-{i}",
                "details": {"i": i},
                "prev_hmac": prev,
            }
            h = _compute_hmac(key, prev, entry)
            entry["hmac"] = h
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            prev = h
            hmacs.append(h)
    return hmacs


def _corrupt_file_via_wrong_key(audit_dir: Path, day: str, count: int) -> None:
    """Seed a jsonl file whose stored HMACs do NOT match the canonical key.

    This is the live-symptom scenario from the 2026-05-13 ticket: the
    file was written under a key that has since been rotated, so its
    HMACs no longer match the active key.
    """
    from bernstein.core.security.audit import _compute_hmac

    wrong_key = b"z" * 64
    log_path = audit_dir / f"{day}.jsonl"
    prev = "0" * 64
    with log_path.open("a", encoding="utf-8", newline="") as fh:
        for i in range(count):
            entry = {
                "timestamp": f"{day}T00:00:{i:02d}.000000Z",
                "event_type": "test.evt",
                "actor": "test",
                "resource_type": "task",
                "resource_id": f"id-{i}",
                "details": {"i": i},
                "prev_hmac": prev,
            }
            h = _compute_hmac(wrong_key, prev, entry)
            entry["hmac"] = h
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            prev = h


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_dry_run_prints_plan_and_moves_nothing(audit_env: Path) -> None:
    """--dry-run shows the candidate list but never touches the filesystem."""
    _corrupt_file_via_wrong_key(audit_env, "2026-04-26", count=2)
    _seed_clean_log(audit_env, "2026-05-14", count=2)

    files_before = sorted(p.name for p in audit_env.glob("*.jsonl"))
    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        ["archive", "--audit-dir", str(audit_env), "--corrupt", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "2026-04-26.jsonl" in result.output
    assert "dry-run" in result.output.lower()
    # The clean file must NOT appear in the plan.
    assert "2026-05-14.jsonl" not in result.output or "selected" in result.output.lower()

    files_after = sorted(p.name for p in audit_env.glob("*.jsonl"))
    assert files_before == files_after, "dry-run must not move any files"
    # No archive dir created on dry-run.
    archive_root = audit_env / "_archived"
    assert not archive_root.exists() or not any(archive_root.iterdir())


# ---------------------------------------------------------------------------
# --corrupt
# ---------------------------------------------------------------------------


def test_corrupt_archives_only_failing_files(audit_env: Path, tmp_path: Path) -> None:
    """--corrupt picks the bad file; the clean one stays put."""
    _corrupt_file_via_wrong_key(audit_env, "2026-04-26", count=2)
    # Chain from genesis so the clean file's HMACs survive removal
    # of the corrupt predecessor.
    _seed_clean_log(audit_env, "2026-05-14", count=2, chain_from_genesis=True)

    archive_dir = tmp_path / "archive-out"

    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        [
            "archive",
            "--audit-dir",
            str(audit_env),
            "--archive-dir",
            str(archive_dir),
            "--corrupt",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    # Bad file moved out.
    assert not (audit_env / "2026-04-26.jsonl").exists()
    # Clean file remained.
    assert (audit_env / "2026-05-14.jsonl").exists()
    # Bad file is at the archive destination.
    moved = archive_dir / "2026-04-26.jsonl"
    assert moved.exists()
    # Sidecar metadata exists and matches.
    meta_path = archive_dir / "2026-04-26.jsonl.archived.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["reason"] == "corrupt-hmac"
    expected_sha = hashlib.sha256(moved.read_bytes()).hexdigest()
    assert meta["sha256_of_archived_file"] == expected_sha


# ---------------------------------------------------------------------------
# --before <date>
# ---------------------------------------------------------------------------


def test_before_archives_correct_subset(audit_env: Path, tmp_path: Path) -> None:
    """--before only catches files with filename date strictly earlier."""
    # Build three clean logs across the divide. The "remains after
    # archive" file (2026-05-14) is chained from genesis so the
    # post-archive verify is clean once the earlier two are moved.
    _seed_clean_log(audit_env, "2026-04-26", count=1)
    _seed_clean_log(audit_env, "2026-05-07", count=1)
    _seed_clean_log(audit_env, "2026-05-14", count=1, chain_from_genesis=True)

    archive_dir = tmp_path / "archive-out"

    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        [
            "archive",
            "--audit-dir",
            str(audit_env),
            "--archive-dir",
            str(archive_dir),
            "--before",
            "2026-05-14",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    # 2026-04-26 and 2026-05-07 are < 2026-05-14, both moved.
    assert (archive_dir / "2026-04-26.jsonl").exists()
    assert (archive_dir / "2026-05-07.jsonl").exists()
    # 2026-05-14 is the cutoff (exclusive lower-than), stays put.
    assert (audit_env / "2026-05-14.jsonl").exists()
    # Each moved file has a metadata sidecar tagged "before-date".
    for name in ("2026-04-26.jsonl", "2026-05-07.jsonl"):
        meta = json.loads((archive_dir / f"{name}.archived.json").read_text())
        assert meta["reason"] == "before-date"


def test_before_rejects_bad_date_format(audit_env: Path) -> None:
    """--before requires YYYY-MM-DD; anything else exits non-zero."""
    _seed_clean_log(audit_env, "2026-05-14", count=1)
    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        ["archive", "--audit-dir", str(audit_env), "--before", "yesterday"],
    )
    assert result.exit_code == 2, result.output
    assert "YYYY-MM-DD" in result.output


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_already_archived_file_refuses_re_archive(audit_env: Path, tmp_path: Path) -> None:
    """Running archive twice into the same dir is refused, not silently overwritten."""
    _corrupt_file_via_wrong_key(audit_env, "2026-04-26", count=2)

    archive_dir = tmp_path / "archive-out"
    runner = CliRunner()

    # First run succeeds.
    result1 = runner.invoke(
        audit_group,
        [
            "archive",
            "--audit-dir",
            str(audit_env),
            "--archive-dir",
            str(archive_dir),
            "--corrupt",
            "--yes",
        ],
    )
    assert result1.exit_code == 0, result1.output

    # Re-seed a new corrupt file with the same filename - simulates an
    # operator who accidentally points two cleanup runs at the same
    # archive dir.
    _corrupt_file_via_wrong_key(audit_env, "2026-04-26", count=2)

    result2 = runner.invoke(
        audit_group,
        [
            "archive",
            "--audit-dir",
            str(audit_env),
            "--archive-dir",
            str(archive_dir),
            "--corrupt",
            "--yes",
        ],
    )
    assert result2.exit_code == 1, result2.output
    assert "Refusing to overwrite" in result2.output
    # The source file must still exist (nothing got moved).
    assert (audit_env / "2026-04-26.jsonl").exists()


# ---------------------------------------------------------------------------
# post-archive verify reporting + exit codes
# ---------------------------------------------------------------------------


def test_post_archive_verify_is_reported_and_passes(audit_env: Path, tmp_path: Path) -> None:
    """After archiving the corrupt file the verify pass succeeds and is shown."""
    _corrupt_file_via_wrong_key(audit_env, "2026-04-26", count=2)
    # Clean chain starts from genesis on what remains.
    _seed_clean_log(audit_env, "2026-05-14", count=2, chain_from_genesis=True)

    archive_dir = tmp_path / "archive-out"

    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        [
            "archive",
            "--audit-dir",
            str(audit_env),
            "--archive-dir",
            str(archive_dir),
            "--corrupt",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Post-archive HMAC chain verification PASSED" in result.output

    # Independent confirmation: call AuditLog.verify() directly.
    audit_log = AuditLog(audit_env)
    valid, errors = audit_log.verify()
    assert valid, f"chain still broken after archive: {errors}"


def test_requires_yes_for_real_move(audit_env: Path, tmp_path: Path) -> None:
    """Without --yes (and without --dry-run), the command refuses to move anything."""
    _corrupt_file_via_wrong_key(audit_env, "2026-04-26", count=2)

    archive_dir = tmp_path / "archive-out"
    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        [
            "archive",
            "--audit-dir",
            str(audit_env),
            "--archive-dir",
            str(archive_dir),
            "--corrupt",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--yes" in result.output
    # Source untouched.
    assert (audit_env / "2026-04-26.jsonl").exists()


def test_archive_help_lists_all_flags() -> None:
    """Smoke-test the --help surface (catches accidental flag rename)."""
    runner = CliRunner()
    result = runner.invoke(audit_group, ["archive", "--help"])
    assert result.exit_code == 0, result.output
    for flag in ("--before", "--corrupt", "--archive-dir", "--audit-dir", "--dry-run", "--yes"):
        assert flag in result.output, f"missing {flag} in --help"


def test_archive_refuses_proc_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Best-effort safety: refuse to operate on /proc/, /dev/, /sys/ paths."""
    # We can't pass a literal /proc path through click.Path(resolve_path=True)
    # because the path must be resolvable. Symlink to /proc instead so the
    # resolution surfaces the unsafe prefix.
    if not os.path.isdir("/proc"):  # pragma: no cover - non-linux skip
        pytest.skip("no /proc on this platform")

    link = tmp_path / "audit-link"
    link.symlink_to("/proc")

    key_path = tmp_path / "audit.key"
    key_path.write_bytes(b"a" * 64)
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    monkeypatch.setenv(AUDIT_KEY_ENV, str(key_path))

    runner = CliRunner()
    result = runner.invoke(
        audit_group,
        ["archive", "--audit-dir", str(link), "--dry-run"],
    )
    assert result.exit_code == 1, result.output
    assert "Refusing to operate" in result.output or "Refusing to archive" in result.output


# ---------------------------------------------------------------------------
# _is_safe_audit_dir boundary (platform-independent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe",
    [
        # Exact mount roots: a symlink to /proc resolves to "/proc" with NO
        # trailing slash, so a startswith("/proc/") check silently lets it
        # through. These exact-root cases are the regression that landed red.
        "/proc",
        "/dev",
        "/sys",
        # Children must also be refused.
        "/proc/self",
        "/dev/null",
        "/sys/kernel",
    ],
)
def test_is_safe_audit_dir_refuses_pseudo_filesystems(unsafe: str) -> None:
    from bernstein.cli.commands.audit_cmd import _is_safe_audit_dir

    safe, reason = _is_safe_audit_dir(Path(unsafe))
    assert safe is False, f"expected {unsafe} to be refused, got safe=True"
    assert "refusing to operate" in reason.lower()


@pytest.mark.parametrize(
    "ok",
    [
        # Lookalike prefixes that merely *start* with the pseudo-fs token must
        # NOT be refused (e.g. /procurement, /devel, /system).
        "/procurement/audit",
        "/devel/audit",
        "/system/audit",
    ],
)
def test_is_safe_audit_dir_allows_lookalike_prefixes(ok: str) -> None:
    from bernstein.cli.commands.audit_cmd import _is_safe_audit_dir

    safe, _reason = _is_safe_audit_dir(Path(ok))
    assert safe is True, f"expected {ok} to be allowed, got safe=False"
