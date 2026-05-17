"""First-run notice idempotence and persistence tests."""

from __future__ import annotations

import io
from pathlib import Path

from bernstein.core.telemetry import first_run_marker_path, is_first_run_acknowledged
from bernstein.core.telemetry.wire import (
    FIRST_RUN_NOTICE,
    maybe_print_first_run_notice,
)


def test_notice_prints_once(tmp_home: Path) -> None:
    buf = io.StringIO()
    assert maybe_print_first_run_notice(home=tmp_home, out=buf) is True
    assert FIRST_RUN_NOTICE in buf.getvalue()


def test_notice_marker_persists(tmp_home: Path) -> None:
    buf = io.StringIO()
    maybe_print_first_run_notice(home=tmp_home, out=buf)
    assert first_run_marker_path(home=tmp_home).exists()
    assert is_first_run_acknowledged(home=tmp_home) is True


def test_notice_does_not_repeat(tmp_home: Path) -> None:
    buf = io.StringIO()
    maybe_print_first_run_notice(home=tmp_home, out=buf)
    buf2 = io.StringIO()
    assert maybe_print_first_run_notice(home=tmp_home, out=buf2) is False
    assert buf2.getvalue() == ""


def test_notice_idempotent_across_many_calls(tmp_home: Path) -> None:
    buf = io.StringIO()
    for _ in range(10):
        maybe_print_first_run_notice(home=tmp_home, out=buf)
    assert buf.getvalue().count("collects no telemetry") == 1


def test_notice_handles_broken_writer(tmp_home: Path) -> None:
    class Broken:
        def write(self, _s: str) -> None:
            raise RuntimeError("boom")

    # Must not raise; falls back internally.
    maybe_print_first_run_notice(home=tmp_home, out=Broken())
    # Marker still gets persisted.
    assert is_first_run_acknowledged(home=tmp_home) is True
