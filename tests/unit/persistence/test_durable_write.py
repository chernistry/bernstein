"""Tests for ``fsynced_write`` context manager."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from bernstein.core.persistence.durable_write import fsynced_write

if TYPE_CHECKING:
    from pathlib import Path


def test_fsynced_write_writes_and_fsyncs_on_clean_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean ``with`` block flushes, fsyncs, then closes the handle."""
    target = tmp_path / "out.jsonl"
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)

    with fsynced_write(target) as handle:
        handle.write("line-1\n")

    assert target.read_text(encoding="utf-8") == "line-1\n"
    assert len(fsync_calls) == 1, "exactly one fsync should fire on clean exit"


def test_fsynced_write_closes_handle_when_block_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception inside the block must still close the handle.

    Simulates a write-then-exception by raising after the caller writes
    to the handle.  The helper must:

    * not fsync (we cannot promise durability for a partial write),
    * still close the handle in ``finally`` so no descriptor leaks,
    * re-raise the original exception.
    """
    target = tmp_path / "out.jsonl"
    captured_handle: list[object] = []
    fsync_calls: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsync_calls.append(fd))

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with fsynced_write(target) as handle:
            captured_handle.append(handle)
            handle.write("partial\n")
            raise _Boom("simulated mid-write failure")

    assert captured_handle, "context manager must yield a handle"
    yielded = captured_handle[0]
    # The handle must be closed by the finally clause.
    assert getattr(yielded, "closed", False) is True, "handle must be closed on exception"
    # fsync must NOT have run - partial writes cannot be promised durable.
    assert fsync_calls == []


def test_fsynced_write_appends_by_default(tmp_path: Path) -> None:
    """Default mode is append; existing content is preserved."""
    target = tmp_path / "out.jsonl"
    target.write_text("existing\n", encoding="utf-8")
    with fsynced_write(target) as handle:
        handle.write("appended\n")
    assert target.read_text(encoding="utf-8") == "existing\nappended\n"


def test_fsynced_write_respects_custom_mode(tmp_path: Path) -> None:
    """Caller can override mode (e.g. ``"w"`` for truncate-write)."""
    target = tmp_path / "out.jsonl"
    target.write_text("stale\n", encoding="utf-8")
    with fsynced_write(target, mode="w") as handle:
        handle.write("fresh\n")
    assert target.read_text(encoding="utf-8") == "fresh\n"


def test_fsynced_write_propagates_original_exception_type(tmp_path: Path) -> None:
    """The original exception type and message reach the caller intact."""
    target = tmp_path / "out.jsonl"

    class _Custom(ValueError):
        pass

    with pytest.raises(_Custom, match="boom"):
        with fsynced_write(target):
            raise _Custom("boom")
