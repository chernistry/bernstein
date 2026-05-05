"""Real-process cluster end-to-end harness.

The tests in this package boot two real OS processes (central server and
worker) and exercise registration, heartbeats, task claims, crash recovery,
network partitions, and token expiry over real HTTP.

Marked with ``cluster_e2e`` and ``slow`` so they are skipped by default in
the regular pytest run; CI executes them via ``cluster-e2e.yml``.
"""
