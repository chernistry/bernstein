"""Lightweight RSS / fd probes for the TC-C stress suite.

Wraps ``psutil`` where available and degrades gracefully when it is
not installed (or refuses on the current platform - e.g. some sandbox
containers strip ``/proc``).  Tests use the helpers below instead of
importing ``psutil`` directly so the suite stays self-contained.
"""

from __future__ import annotations

import gc
import os
import platform
import sys
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

try:  # pragma: no cover - import guard
    import psutil as _psutil  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised on minimal installs
    _psutil = None  # type: ignore[assignment]


def have_psutil() -> bool:
    """Return True when the ``psutil`` package is importable."""

    return _psutil is not None


def _proc() -> Any | None:
    if _psutil is None:
        return None
    try:
        return _psutil.Process(os.getpid())
    except Exception:  # pragma: no cover - defensive
        return None


def rss_bytes() -> int | None:
    """Return current resident-set size in bytes, or ``None`` if unavailable.

    Prefers ``psutil`` for cross-platform accuracy.  Falls back to
    ``resource.getrusage`` on POSIX (Linux returns KiB, macOS returns
    bytes - we normalise to bytes).  Returns ``None`` on Windows when
    ``psutil`` is unavailable so the caller can skip the assertion.
    """

    proc = _proc()
    if proc is not None:
        with suppress(Exception):
            return int(proc.memory_info().rss)

    if platform.system() == "Windows":  # pragma: no cover
        return None

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
    except (ImportError, OSError):  # pragma: no cover
        return None

    ru_maxrss = int(usage.ru_maxrss)
    if sys.platform == "darwin":
        # macOS reports bytes directly.
        return ru_maxrss
    # Linux + BSD: KiB.
    return ru_maxrss * 1024


def fd_count() -> int | None:
    """Return the count of open file descriptors for this process.

    Returns ``None`` when neither ``psutil`` nor ``/proc/self/fd`` are
    available (e.g. Windows without ``psutil``).  ``num_fds()`` on
    Windows raises ``AccessDenied`` for some sandboxed hosts; we treat
    that as "unsupported here" and skip the related assertion.
    """

    proc = _proc()
    if proc is not None:
        with suppress(AttributeError, Exception):
            return int(proc.num_fds())

    try:
        return len(os.listdir("/proc/self/fd"))
    except (FileNotFoundError, PermissionError):  # pragma: no cover
        return None


def settle(rounds: int = 3) -> None:
    """Force aggressive GC to settle generational caches before sampling.

    Memory probes are noisy when invoked immediately after a tight
    Python loop - recent allocations linger on the freelist until the
    next major collection.  Calling ``gc.collect()`` a few times reduces
    the noise floor without introducing flakiness.
    """

    for _ in range(rounds):
        gc.collect()


def children_pids(parent_pid: int | None = None) -> Iterable[int]:
    """Yield direct child PIDs of *parent_pid* (default: this process).

    Returns an empty iterable when ``psutil`` is missing or the lookup
    fails - callers handle the "no introspection" case explicitly so
    the missing-psutil fallback path stays visible.
    """

    if _psutil is None:
        return ()
    try:
        parent = _psutil.Process(parent_pid or os.getpid())
        return [child.pid for child in parent.children(recursive=False)]
    except Exception:  # pragma: no cover - defensive
        return ()
