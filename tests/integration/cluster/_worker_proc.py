"""Minimal worker subprocess for the concurrent-claim race scenario.

This is a deliberately tiny worker: register, mint a JWT, attempt to claim
a specific task ID with ``expected_version=1``, write the HTTP status to
``--result-file``. It is *not* a substitute for ``bernstein worker``; it
only exists to prove that the central server's CAS-style claim works
across two real OS processes.

Usage:
    python -m tests.integration.cluster._worker_proc \
        --server http://127.0.0.1:8052 \
        --task-id <id> \
        --token <jwt> \
        --result-file /tmp/worker-a.txt
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx


def main() -> int:
    """Entry point for the race-worker subprocess."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--start-at", type=float, default=0.0)
    parser.add_argument("--expected-version", type=int, default=1)
    args = parser.parse_args()

    # Spin until the agreed wall-clock start time, so both workers race
    # the claim within a few microseconds of each other.
    if args.start_at > 0:
        while time.time() < args.start_at:
            time.sleep(0.001)

    headers = {"Authorization": f"Bearer {args.token}"}
    url = f"{args.server.rstrip('/')}/tasks/{args.task_id}/claim?expected_version={args.expected_version}"
    try:
        resp = httpx.post(url, headers=headers, timeout=5.0)
        status = resp.status_code
        body = resp.text[:200]
    except httpx.HTTPError as exc:
        status = -1
        body = f"transport-error: {exc}"

    with open(args.result_file, "w", encoding="utf-8") as fh:
        fh.write(f"{status}\n{body}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
