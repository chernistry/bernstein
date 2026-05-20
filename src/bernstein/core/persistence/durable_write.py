"""Context manager that bundles append + fsync for JSONL durability.

Append-only JSONL files (``tasks.jsonl``, ``archive.jsonl``, per-task
progress logs) rely on per-line atomicity at the OS layer plus a
trailing ``os.fsync`` to push the bytes from the page cache to stable
storage before the caller's mutation is considered durable.

The naive open/write/flush/fsync sequence is correct on the happy path
but has a subtle ordering bug: when the ``fsync`` happens *outside* the
``with`` block, an exception raised inside the block closes the handle
without fsyncing.  The handle is gone but the data may still be
sitting in the page cache, so a subsequent SIGKILL / power loss can
silently drop the trailing bytes.

This helper centralises the "open, write, fsync on clean exit, close
in finally" sequence so every JSONL appender follows the same shape.

Typical usage::

    from bernstein.core.persistence.durable_write import fsynced_write

    with fsynced_write(self._jsonl_path) as handle:
        handle.write(line)

On a clean exit the handle is flushed, ``os.fsync`` is invoked, and
the handle is closed.  On an exception the fsync is skipped (we cannot
promise durability for partial bytes) but the handle is still closed
so no descriptor leaks.  Re-raises the original exception.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from typing import IO

logger = logging.getLogger(__name__)


@contextmanager
def fsynced_write(
    path: Path,
    *,
    mode: str = "a",
    encoding: str = "utf-8",
) -> Iterator[IO[str]]:
    """Yield an open text file handle that is fsynced on clean exit.

    This helper is text-only; it is intended for JSONL appenders where
    every record is a UTF-8 line. The handle is opened with the given
    ``mode`` (defaults to ``"a"`` for append-only JSONL files) and
    ``encoding`` (defaults to UTF-8). On clean exit the handle is
    flushed and ``os.fsync(handle.fileno())`` is invoked so the bytes
    reach stable storage before the context returns. On an exception
    the fsync is skipped (partial writes cannot be promised durable)
    but the handle is closed in the ``finally`` clause regardless so no
    descriptor leaks.

    Args:
        path: File to open.  Parent directories must already exist;
            this helper does not create them so callers retain control
            over the directory layout.
        mode: File mode passed to ``open()``.  Must be a text mode;
            defaults to ``"a"``.
        encoding: Text encoding passed to ``open()``.  Defaults to
            ``"utf-8"``.

    Yields:
        The open text-mode file handle.  Callers write ``str`` lines to
        it as usual; the helper owns flush, fsync, and close.

    Raises:
        Any exception raised by the caller's block is re-raised
        unchanged after the handle is closed.
    """
    handle: IO[str] = path.open(mode, encoding=encoding)
    try:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()


__all__ = ["fsynced_write"]
