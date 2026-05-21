"""Subprocess hygiene under signal pressure.

Tests assert:

* No zombie processes survive after we kill our "orchestrator" stand-in
* SIGTERM produces a clean shutdown within a 5 s budget
* SIGKILL fallback kicks in when a child ignores SIGTERM

The "orchestrator" here is just a Python parent that holds ``Popen``
handles - we exercise the generic OS contract our real orchestrator
depends on, so a regression in the platform layer (e.g. someone
swaps ``waitpid`` for a fire-and-forget call) trips this gate.

Windows is skipped: ``SIGTERM`` and ``SIGKILL`` semantics don't apply.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

import pytest

from tests.stress._probes import children_pids, have_psutil

pytestmark = [pytest.mark.stress, pytest.mark.timeout(60)]

_SKIP_WINDOWS = sys.platform.startswith("win")


def _spawn_idle_child(*, ignore_sigterm: bool = False) -> subprocess.Popen[bytes]:
    """Spawn a Python child that sleeps forever (optionally ignoring SIGTERM)."""

    if ignore_sigterm:
        code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
    else:
        code = "import time; time.sleep(120)"
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(_SKIP_WINDOWS, reason="POSIX signals only")
def test_killed_orchestrator_leaves_no_zombies() -> None:
    """Spawning 20 workers and killing them must leave no <defunct> entries.

    A zombie is a child that exited but whose exit status was never
    reaped via ``waitpid``.  In production a zombie pile-up exhausts
    the host's process table and stalls every future ``fork()``.  We
    spawn 20 short-lived children, terminate each, ``wait()``, then
    assert that ``psutil.children()`` returns an empty list.
    """

    if not have_psutil():
        pytest.skip("psutil required to inspect child status")

    children: list[subprocess.Popen[bytes]] = [_spawn_idle_child() for _ in range(20)]
    try:
        # Verify all children are alive before we kill them.
        time.sleep(0.1)
        assert len(list(children_pids())) >= 20, "expected 20+ direct children before signalling"

        for child in children:
            child.terminate()
        for child in children:
            try:
                child.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2.0)
    finally:
        for child in children:
            if child.poll() is None:  # pragma: no cover - defensive
                child.kill()
                child.wait(timeout=2.0)

    # Force at least one event-loop tick on the parent so the OS has a
    # chance to reap any lingering exit status.
    time.sleep(0.1)

    # Cross-check: psutil's children() filters out zombies on most
    # platforms, so we additionally scan for STATUS_ZOMBIE explicitly.
    import psutil  # type: ignore[import-not-found]

    me = psutil.Process(os.getpid())
    zombies: list[int] = []
    for child in me.children(recursive=True):
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                zombies.append(child.pid)
        except psutil.NoSuchProcess:
            continue
    assert zombies == [], f"zombie processes remained after wait(): {zombies}"


@pytest.mark.skipif(_SKIP_WINDOWS, reason="POSIX signals only")
def test_sigterm_clean_shutdown_within_budget() -> None:
    """A cooperative child must exit within 5 s of SIGTERM.

    Catches: regressions where the worker swallows SIGTERM with a
    catch-all signal handler that never re-raises or sets a shutdown
    flag.  Five seconds is the hard upper bound; the cooperative path
    typically returns within ~50 ms.
    """

    child = _spawn_idle_child(ignore_sigterm=False)
    try:
        time.sleep(0.05)  # let the child install its default handler
        deadline = time.monotonic() + 5.0
        child.terminate()
        try:
            rc = child.wait(timeout=5.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - failure branch
            child.kill()
            child.wait(timeout=2.0)
            pytest.fail("cooperative SIGTERM did not exit within 5 s")
        elapsed = time.monotonic() - deadline + 5.0  # ≤ 5 s by construction
        assert rc is not None
        assert elapsed <= 5.0, f"clean shutdown elapsed={elapsed:.2f}s > 5 s"
    finally:
        if child.poll() is None:  # pragma: no cover - defensive
            child.kill()
            child.wait(timeout=2.0)


@pytest.mark.skipif(_SKIP_WINDOWS, reason="POSIX signals only")
def test_sigkill_fallback_after_sigterm_ignored() -> None:
    """A child that ignores SIGTERM must still die when we follow up with SIGKILL.

    Catches: an "always-graceful" code path that loops forever waiting
    for a SIGTERM that never lands.  SIGKILL is uncatchable, so the
    fallback must always succeed within ~1 s after we issue it.
    """

    child = _spawn_idle_child(ignore_sigterm=True)
    try:
        # Give the child time to install the SIG_IGN handler.
        time.sleep(0.1)
        child.terminate()
        # Confirm SIGTERM was indeed ignored - the child should still be
        # alive after a short pause.
        with suppress(subprocess.TimeoutExpired):
            child.wait(timeout=0.5)
            pytest.fail("child unexpectedly exited from SIGTERM (test invalid)")

        # SIGKILL fallback - must reap within 1 s.
        start = time.monotonic()
        child.kill()
        rc = child.wait(timeout=2.0)
        elapsed = time.monotonic() - start
        assert rc is not None
        assert elapsed < 2.0, f"SIGKILL fallback elapsed={elapsed:.2f}s"
        # On POSIX, SIGKILL produces returncode -9.
        assert rc == -signal.SIGKILL or rc < 0, f"unexpected rc={rc}"
    finally:
        if child.poll() is None:  # pragma: no cover - defensive
            child.kill()
            child.wait(timeout=2.0)
