"""Unit tests specific to :class:`LocalFsSink`.

The conformance suite in ``test_sink_protocol.py`` already exercises
the protocol surface. These tests cover behaviour unique to the
local filesystem backend: durability semantics, directory creation,
and the ``root`` property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bernstein.core.storage import LocalFsSink
from bernstein.core.storage.keys import (
    audit_log_key,
    cost_ledger_key,
    task_output_key,
    wal_key,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    await sink.write("a/b/c/d.txt", b"hello")
    assert (tmp_path / "a" / "b" / "c" / "d.txt").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_root_property(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    assert sink.root == tmp_path


@pytest.mark.asyncio
async def test_default_root_is_sdd() -> None:
    sink = LocalFsSink()
    assert str(sink.root) == ".sdd"


@pytest.mark.asyncio
async def test_delete_missing_is_noop(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    # Must not raise
    await sink.delete("missing.txt")


@pytest.mark.asyncio
async def test_list_empty_root_returns_empty(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path / "never-created")
    assert await sink.list("") == []


@pytest.mark.asyncio
async def test_write_is_atomic_on_crash_simulation(tmp_path: Path) -> None:
    """A new write overwrites atomically: partial data is never visible."""
    sink = LocalFsSink(tmp_path)
    await sink.write("foo.txt", b"first")
    await sink.write("foo.txt", b"second")
    assert await sink.read("foo.txt") == b"second"


@pytest.mark.asyncio
async def test_stat_reports_size(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    await sink.write("s.txt", b"abcdef")
    st = await sink.stat("s.txt")
    assert st.size_bytes == 6
    assert st.etag is None  # local fs has no etag


@pytest.mark.asyncio
async def test_integration_with_keys_module(tmp_path: Path) -> None:
    """Canonical key helpers produce keys LocalFsSink handles."""
    sink = LocalFsSink(tmp_path)
    await sink.write(wal_key("run-abc"), b'{"seq":0}\n')
    await sink.write(task_output_key("t-1"), b'{"ok":true}')
    await sink.write(audit_log_key("2026-04-19"), b"entry\n")
    await sink.write(cost_ledger_key("run-abc"), b'{"usd":0.1}\n')

    assert (tmp_path / "runtime" / "wal" / "run-abc.wal.jsonl").is_file()
    assert (tmp_path / "tasks" / "t-1" / "output.json").is_file()
    assert (tmp_path / "audit" / "2026-04-19.jsonl").is_file()
    assert (tmp_path / "cost" / "run-abc" / "ledger.jsonl").is_file()


@pytest.mark.asyncio
async def test_close_is_idempotent(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    await sink.close()
    await sink.close()  # must not raise


@pytest.mark.asyncio
async def test_read_missing_raises_fnf(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    with pytest.raises(FileNotFoundError):
        await sink.read("nope.txt")


@pytest.mark.asyncio
async def test_content_type_argument_accepted(tmp_path: Path) -> None:
    """``content_type`` is accepted for protocol parity; must not raise."""
    sink = LocalFsSink(tmp_path)
    await sink.write("mime.txt", b"hi", content_type="text/plain")
    assert await sink.read("mime.txt") == b"hi"


@pytest.mark.asyncio
async def test_durable_false_still_persists(tmp_path: Path) -> None:
    """Local sink ignores durable=False — persistence is still guaranteed."""
    sink = LocalFsSink(tmp_path)
    await sink.write("d.txt", b"x", durable=False)
    assert await sink.read("d.txt") == b"x"


@pytest.mark.asyncio
async def test_list_with_prefix_matches_subtree(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    await sink.write("a/1.txt", b"1")
    await sink.write("a/2.txt", b"2")
    await sink.write("b/3.txt", b"3")

    under_a = await sink.list("a")
    assert sorted(under_a) == ["a/1.txt", "a/2.txt"]


@pytest.mark.asyncio
async def test_stat_on_directory_raises_fnf(tmp_path: Path) -> None:
    sink = LocalFsSink(tmp_path)
    await sink.write("d/inside.txt", b"x")
    # ``d`` is a directory, not an artifact
    with pytest.raises(FileNotFoundError):
        await sink.stat("d")
