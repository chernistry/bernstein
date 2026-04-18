"""Regression tests for audit-068: api_usage JSONL files must rotate.

Before the fix, ``api_usage_YYYYMMDD.jsonl`` grew unbounded — multi-GB files
after long-running orchestration sessions. The writer now rotates at a size
threshold and bounds the number of historical backups.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.observability import metric_collector as mc
from bernstein.core.observability.metric_collector import (
    MetricsCollector,
    MetricType,
    iter_metric_files,
)


def _all_lines(files: list[Path]) -> list[str]:
    out: list[str] = []
    for path in files:
        out.extend(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return out


def test_metric_writer_rotates_when_threshold_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Driving the writer past the rotation threshold creates bounded backups.

    All records must remain reachable via ``iter_metric_files`` — the glob
    helper is responsible for stitching live + rotated files back together.
    """
    metrics_dir = tmp_path / "metrics"
    collector = MetricsCollector(metrics_dir=metrics_dir)

    # Force aggressive rotation so the test stays fast: 512 B cap, 5 backups.
    rotate_bytes = 512
    max_backups = 5
    monkeypatch.setattr(mc, "_METRIC_FILE_ROTATE_BYTES", rotate_bytes)
    monkeypatch.setattr(mc, "_METRIC_FILE_MAX_BACKUPS", max_backups)
    # Flush on every record — we want deterministic rotation, not buffered.
    collector._buffer_limit = 1

    payload_label = "x" * 256  # ~300+ bytes per JSONL line after framing

    # Every record is larger than the rotation threshold so each write forces
    # a fresh rollover — live + 5 backups will hold exactly 6 records.
    total_records = 6
    for i in range(total_records):
        collector._write_metric_point(
            MetricType.API_USAGE,
            float(i),
            {"task_id": f"t-{i}", "payload": payload_label},
        )
    collector.flush()

    files = iter_metric_files(metrics_dir, "api_usage")
    assert files, "expected at least the live api_usage file to exist"

    # Rotation must have happened at least once — live file plus backups.
    rotated = [p for p in files if p.suffix != ".jsonl"]
    assert rotated, "no rotated backups were produced despite exceeding threshold"

    # Backup count must not exceed the configured retention.
    assert len(rotated) <= max_backups, f"retention breached: {[p.name for p in rotated]}"

    # No individual file may exceed ~2x the threshold — the append happens
    # after rotation, so a single write can at most be one line over.
    for path in files:
        assert path.stat().st_size <= 2 * rotate_bytes, (
            f"{path.name} is {path.stat().st_size} bytes, over threshold {rotate_bytes}"
        )

    # Reading live + rotated backups concatenated must recover every record.
    lines = _all_lines(files)
    values = sorted(int(json.loads(line)["value"]) for line in lines)
    assert values == list(range(total_records))


def test_metric_writer_bounds_disk_under_sustained_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Long-running workloads must not accumulate unbounded JSONL bytes.

    Retention is the safety valve: oldest rollovers get dropped, keeping the
    total on-disk footprint proportional to (backups + 1) * threshold.
    """
    metrics_dir = tmp_path / "metrics"
    collector = MetricsCollector(metrics_dir=metrics_dir)

    rotate_bytes = 1024
    max_backups = 3
    monkeypatch.setattr(mc, "_METRIC_FILE_ROTATE_BYTES", rotate_bytes)
    monkeypatch.setattr(mc, "_METRIC_FILE_MAX_BACKUPS", max_backups)
    collector._buffer_limit = 1

    payload_label = "x" * 256
    for i in range(200):
        collector._write_metric_point(
            MetricType.API_USAGE,
            float(i),
            {"task_id": f"t-{i}", "payload": payload_label},
        )
    collector.flush()

    files = iter_metric_files(metrics_dir, "api_usage")
    rotated = [p for p in files if p.suffix != ".jsonl"]
    assert len(rotated) <= max_backups
    total_bytes = sum(p.stat().st_size for p in files)
    # Live file + max_backups rotated files, each up to ~2x threshold.
    assert total_bytes <= 2 * rotate_bytes * (max_backups + 1)


def test_iter_metric_files_includes_rotated_backups(tmp_path: Path) -> None:
    """``iter_metric_files`` must return both the live file and ``.N`` backups."""
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "api_usage_20260417.jsonl").write_text("live\n", encoding="utf-8")
    (metrics_dir / "api_usage_20260417.jsonl.1").write_text("rot1\n", encoding="utf-8")
    (metrics_dir / "api_usage_20260417.jsonl.2").write_text("rot2\n", encoding="utf-8")
    # Unrelated file must not be picked up.
    (metrics_dir / "error_rate_20260417.jsonl").write_text("noise\n", encoding="utf-8")

    found = [p.name for p in iter_metric_files(metrics_dir, "api_usage")]
    assert found == [
        "api_usage_20260417.jsonl",
        "api_usage_20260417.jsonl.1",
        "api_usage_20260417.jsonl.2",
    ]


def test_iter_metric_files_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert iter_metric_files(tmp_path / "does-not-exist", "api_usage") == []
