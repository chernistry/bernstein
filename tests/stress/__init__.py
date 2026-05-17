"""Stress + resource-leak detection tests (TC-C).

Catches the class of bug that only surfaces under sustained operation:

* Slow memory growth from accumulating refs
* File-descriptor leaks
* Zombie subprocesses
* Lock-contention degradation under threaded load
* Append-loop throughput collapse

Every test in this package is marked ``@pytest.mark.stress`` so the
default CI run skips it.  The nightly stress workflow opts in via
``pytest -m stress``.

Tests degrade gracefully when ``psutil`` is missing (the RSS / fd
probes fall back to ``resource.getrusage`` / ``/proc/self/fd`` when
available, otherwise the relevant assertion is skipped).
"""

from __future__ import annotations
