"""Local queue rotation tests."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from bernstein.core.telemetry import (
    Client,
    DailyActivePayload,
    TelemetryEvent,
    queue_path,
    read_recent_events,
    write_enabled,
)
from bernstein.core.telemetry.client import QUEUE_ROTATION_DAYS


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda r: httpx.Response(200))


def _client(tmp_home: Path) -> Client:
    return Client(
        env={},
        home=tmp_home,
        endpoint="http://test.invalid/v1/events",
        http_client=httpx.Client(transport=_ok_transport()),
    )


def test_queue_appends_one_line(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    c = _client(tmp_home)
    c.emit(TelemetryEvent.DAILY_ACTIVE, DailyActivePayload(day_iso="2026-05-17"))
    lines = queue_path(home=tmp_home).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["name"] == "daily_active"


def test_queue_rotation_after_seven_days(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    c = _client(tmp_home)
    c.emit(TelemetryEvent.DAILY_ACTIVE, DailyActivePayload(day_iso="2026-05-01"))
    # Backdate mtime by > 7 days.
    path = queue_path(home=tmp_home)
    old_time = time.time() - (QUEUE_ROTATION_DAYS + 1) * 86400
    import os as _os

    _os.utime(path, (old_time, old_time))
    # Next emit should rotate.
    c.emit(TelemetryEvent.DAILY_ACTIVE, DailyActivePayload(day_iso="2026-05-17"))
    archives = list(tmp_home.glob(".bernstein/telemetry-queue.*.jsonl"))
    assert len(archives) >= 1
    # New queue file has one line.
    fresh = path.read_text(encoding="utf-8").splitlines()
    assert len(fresh) == 1


def test_queue_no_rotation_within_seven_days(tmp_home: Path) -> None:
    write_enabled(True, home=tmp_home)
    c = _client(tmp_home)
    c.emit(TelemetryEvent.DAILY_ACTIVE, DailyActivePayload(day_iso="2026-05-17"))
    c.emit(TelemetryEvent.DAILY_ACTIVE, DailyActivePayload(day_iso="2026-05-18"))
    archives = list(tmp_home.glob(".bernstein/telemetry-queue.*.jsonl"))
    assert archives == []
    assert len(queue_path(home=tmp_home).read_text(encoding="utf-8").splitlines()) == 2


def test_read_recent_events_filters_by_date(tmp_home: Path) -> None:
    """Lines older than the cutoff are excluded."""
    write_enabled(True, home=tmp_home)
    qp = queue_path(home=tmp_home)
    qp.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=UTC).date().isoformat()
    long_ago = (datetime.now(tz=UTC) - timedelta(days=400)).date().isoformat()
    qp.write_text(
        "\n".join(
            [
                json.dumps({"name": "a", "timestamp": f"{long_ago}T00:00:00+00:00"}),
                json.dumps({"name": "b", "timestamp": f"{today}T00:00:00+00:00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = list(read_recent_events(days=30, home=tmp_home))
    parsed = [json.loads(line) for line in out]
    names = [p.get("name") for p in parsed]
    assert "b" in names
    assert "a" not in names


def test_read_recent_events_missing_queue(tmp_home: Path) -> None:
    out = list(read_recent_events(days=30, home=tmp_home))
    assert out == []


def test_read_recent_events_blank_lines_ignored(tmp_home: Path) -> None:
    qp = queue_path(home=tmp_home)
    qp.parent.mkdir(parents=True, exist_ok=True)
    qp.write_text("\n\n\n", encoding="utf-8")
    assert list(read_recent_events(days=30, home=tmp_home)) == []
