"""Tests for runtime state helpers used by Track B features."""

from __future__ import annotations

from pathlib import Path

from bernstein.core.runtime_state import (
    SessionReplayMetadata,
    read_session_replay_metadata,
    rotate_log_file,
    write_session_replay_metadata,
)


def test_rotate_log_file_moves_large_log(tmp_path: Path) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("x" * 32, encoding="utf-8")

    rotated = rotate_log_file(log_path, max_bytes=16)

    assert rotated is True
    assert not log_path.exists()
    assert (tmp_path / "server.log.1").exists()


def test_rotate_log_file_keeps_multiple_backups(tmp_path: Path) -> None:
    """Multiple rotations shift older backups and bound the on-disk set."""
    log_path = tmp_path / "api_usage_20260417.jsonl"

    # Trigger rotation four times with distinguishable contents. Each call
    # writes enough bytes to cross the 16-byte threshold.
    for marker in ("gen-0", "gen-1", "gen-2", "gen-3"):
        log_path.write_text(marker + "-" + ("x" * 32), encoding="utf-8")
        assert rotate_log_file(log_path, max_bytes=16, max_backups=3) is True
        assert not log_path.exists()

    backups = sorted(tmp_path.glob("api_usage_20260417.jsonl.*"))
    # Only .1, .2, .3 survive — older rollovers are deleted.
    assert [p.name for p in backups] == [
        "api_usage_20260417.jsonl.1",
        "api_usage_20260417.jsonl.2",
        "api_usage_20260417.jsonl.3",
    ]
    # Newest-first ordering: the most recently rotated file is .1.
    assert "gen-3" in (tmp_path / "api_usage_20260417.jsonl.1").read_text(encoding="utf-8")
    assert "gen-2" in (tmp_path / "api_usage_20260417.jsonl.2").read_text(encoding="utf-8")
    assert "gen-1" in (tmp_path / "api_usage_20260417.jsonl.3").read_text(encoding="utf-8")


def test_rotate_log_file_skips_small_files(tmp_path: Path) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("tiny", encoding="utf-8")

    assert rotate_log_file(log_path, max_bytes=1024, max_backups=3) is False
    assert log_path.exists()
    assert not (tmp_path / "server.log.1").exists()


def test_session_replay_metadata_round_trip(tmp_path: Path) -> None:
    metadata = SessionReplayMetadata(
        run_id="run-123",
        started_at=123.0,
        git_sha="abcdef123456",
        git_branch="main",
        config_hash="deadbeef",
        seed_path="bernstein.yaml",
    )

    write_session_replay_metadata(tmp_path / ".sdd", metadata)
    loaded = read_session_replay_metadata(tmp_path / ".sdd" / "runs" / "run-123")

    assert loaded == metadata
