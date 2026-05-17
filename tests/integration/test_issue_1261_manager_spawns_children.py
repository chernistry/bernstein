"""Regression smoke test for issue #1261.

Reproduces the failure mode reported in #1261 in a CI-friendly form:

    bernstein run --from-plan ... --auto-approve --audit

with a multi-task plan that *requires* the orchestrator to bring up the
task server, authenticate from spawned worker subprocesses, and then mark
every task done. Issue #1261 reports that the manager subprocess cannot
authenticate to the local task server -- it spends its budget probing
the OpenAPI spec and looking for an auth token on disk, then gets killed
by the 60s watchdog. Result: no child tasks created, no work done.

The test exercises:

1. ``bernstein init`` programmatically (tempdir workspace).
2. ``bernstein run`` with a multi-step plan executed under the ``mock``
   adapter (no LLM credits burned, CI-friendly, no external network).
3. Assertion that more than one task ran end-to-end.
4. Assertion that the audit chain still verifies cleanly.

The test is marked ``xfail(strict=False)`` until the sibling auth-fix PR
for #1261 lands. The steward removes the marker once the fix merges --
at which point the test must pass.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

# Tight timeout: a healthy run with the mock adapter completes in well
# under 30s. If the manager hangs the way #1261 describes (60s watchdog
# kill loop), we want the test to fail fast, not wait the full 60s.
RUN_TIMEOUT_SECONDS = 60

# A multi-task plan: two sequential stages, two steps total. This
# mirrors the issue's "manager that needs to spawn children" setup by
# requiring the orchestrator to drive >1 task through the server in one
# run.
PLAN_YAML = textwrap.dedent(
    """
    name: "issue-1261-smoke"
    description: >
      Multi-task plan that forces the orchestrator to authenticate from
      every spawned worker. Used by the #1261 regression smoke test.

    stages:
      - name: "setup"
        steps:
          - title: "create marker"
            description: "Create a marker file proving the worker ran."
            role: backend
            scope: small
            complexity: low

      - name: "followup"
        depends_on: ["setup"]
        steps:
          - title: "second worker"
            description: "Second worker to verify >1 spawn per run."
            role: backend
            scope: small
            complexity: low
    """
).strip()


def _bernstein_entrypoint() -> list[str]:
    """Build the command prefix used to invoke bernstein.

    Prefer running the installed console script via ``python -m`` against
    the same interpreter the tests run under -- this avoids picking up a
    stale ``bernstein`` shim from the operator's ``~/.local/bin``.
    """
    return [sys.executable, "-m", "bernstein"]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* with stdout+stderr captured, raising a readable error on timeout."""
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        pytest.fail(
            f"command timed out after {timeout:.0f}s: {' '.join(cmd)}\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
        )


@pytest.fixture()
def smoke_workspace(tmp_path: Path) -> Iterator[Path]:
    """Spin up an isolated tempdir workspace seeded by ``bernstein init``.

    Yields the workspace root. Cleans up any orchestrator/server PIDs the
    run may have left behind so a subsequent test does not inherit a
    half-dead background process.
    """
    workspace = tmp_path / "bernstein-poc-1261"
    workspace.mkdir()

    # Initialise a minimal git repo -- bernstein init insists on one.
    subprocess.run(["git", "init", "-b", "main"], cwd=str(workspace), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(workspace), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(workspace), check=True)
    (workspace / "README.md").write_text("# 1261 smoke\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(workspace), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(workspace), check=True)

    env = _smoke_env(workspace)

    init_proc = _run(
        [*_bernstein_entrypoint(), "init"],
        cwd=workspace,
        env=env,
        timeout=60,
    )
    assert init_proc.returncode == 0, f"bernstein init failed:\nstdout={init_proc.stdout}\nstderr={init_proc.stderr}"
    assert (workspace / ".sdd").is_dir(), "bernstein init did not create .sdd/"
    assert (workspace / "bernstein.yaml").is_file(), "bernstein init did not create bernstein.yaml"

    try:
        yield workspace
    finally:
        _kill_lingering_pids(workspace)


def _smoke_env(workspace: Path) -> dict[str, str]:
    """Build the env passed to bernstein subprocess invocations.

    We deliberately keep auth *enabled* (no ``BERNSTEIN_AUTH_DISABLED``)
    because the bug in #1261 is precisely that the manager cannot
    authenticate. The autouse fixture in ``tests/conftest.py`` only sets
    that var on the pytest process, not on the subprocess env we build
    here.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("BERNSTEIN_")}
    env.update(
        {
            "BERNSTEIN_NO_SPLASH": "1",
            # Force the mock adapter so we never hit a real LLM.
            "BERNSTEIN_CLI": "mock",
            # Keep watchdogs short so the test fails fast on a hang.
            "BERNSTEIN_HEARTBEAT_TIMEOUT": "20",
            "BERNSTEIN_MAX_TASK_RETRIES": "0",
            # Point HOME at the workspace so the run doesn't pollute the
            # operator's real home (audit keys, config, etc.).
            "HOME": str(workspace),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _kill_lingering_pids(workspace: Path) -> None:
    """Best-effort kill of any orchestrator/server PIDs the run left behind."""
    runtime = workspace / ".sdd" / "runtime"
    if not runtime.is_dir():
        return
    for pid_file in runtime.glob("*.pid"):
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            continue
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)


def _read_audit_events(workspace: Path) -> list[dict[str, object]]:
    """Return every record from the per-day audit jsonl files."""
    audit_dir = workspace / ".sdd" / "audit"
    events: list[dict[str, object]] = []
    if not audit_dir.is_dir():
        return events
    for jsonl in sorted(audit_dir.glob("*.jsonl")):
        for raw in jsonl.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def _server_log_text(workspace: Path) -> str:
    """Return the orchestrator/server log contents (empty if none)."""
    runtime = workspace / ".sdd" / "runtime"
    chunks: list[str] = []
    for name in ("orchestrator.log", "server.log"):
        path = runtime / name
        if path.is_file():
            chunks.append(f"\n--- {name} ---\n{path.read_text(errors='replace')}")
    return "".join(chunks)


@pytest.mark.xfail(
    reason="blocked on #1261 auth fix -- remove this xfail when the sibling PR merges",
    strict=False,
)
def test_manager_spawns_children_end_to_end(smoke_workspace: Path) -> None:
    """End-to-end regression for #1261.

    Asserts:
      1. ``bernstein run --from-plan ... --auto-approve --audit`` finishes
         within ``RUN_TIMEOUT_SECONDS`` (vs the 60s watchdog hang in #1261).
      2. The plan produces at least 2 lifecycle entries in the task
         server -- i.e. the manager/orchestrator successfully spawned
         more than one worker.
      3. At least one child worker reported a completion (status=done).
      4. The audit chain (``bernstein audit verify``) is clean.
    """
    workspace = smoke_workspace
    plan_path = workspace / "plan.yaml"
    plan_path.write_text(PLAN_YAML + "\n")

    env = _smoke_env(workspace)

    # ``bernstein run <plan.yaml>`` takes the YAML stages plan as a
    # positional arg; ``--from-plan`` is for the JSON/markdown plan
    # archive format produced by ``--plan-only``. The issue's repro used
    # ``--from-plan`` against a markdown plan, but the manager spawn
    # behaviour exercised here is identical for both entry points.
    start = time.monotonic()
    proc = _run(
        [
            *_bernstein_entrypoint(),
            "run",
            str(plan_path),
            "--auto-approve",
            "--audit",
            "--max-cost-usd",
            "1",
            "--quiet",
        ],
        cwd=workspace,
        env=env,
        timeout=RUN_TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - start

    # Assertion 1: command exited cleanly (no watchdog kill).
    server_log = _server_log_text(workspace)
    assert proc.returncode == 0, (
        f"bernstein run exited {proc.returncode} after {elapsed:.1f}s\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}\n"
        f"{server_log}"
    )

    # Assertion 2: at least two tasks reached the task server.
    tasks_jsonl = workspace / ".sdd" / "runtime" / "tasks.jsonl"
    assert tasks_jsonl.is_file(), (
        f"task server never wrote tasks.jsonl -- manager likely never authenticated.\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n{server_log}"
    )
    task_records: list[dict[str, object]] = []
    for raw in tasks_jsonl.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            task_records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    seen_task_ids = {str(rec.get("id")) for rec in task_records if rec.get("id")}
    assert len(seen_task_ids) >= 2, (
        f"expected >= 2 tasks in tasks.jsonl, got {len(seen_task_ids)}: {seen_task_ids}\n"
        f"This matches #1261: the manager couldn't authenticate and never spawned children.\n"
        f"{server_log}"
    )

    # Assertion 3: at least one child task completed.
    done_states = {"done", "completed", "closed"}
    done_records = [rec for rec in task_records if str(rec.get("status", "")).lower() in done_states]
    assert done_records, (
        f"no task reached a 'done' state. task statuses observed: "
        f"{sorted({str(r.get('status')) for r in task_records})}\n{server_log}"
    )

    # Assertion 4: audit chain verifies cleanly.
    audit_events = _read_audit_events(workspace)
    assert audit_events, "audit log is empty -- expected at least the genesis event"

    verify_proc = _run(
        [*_bernstein_entrypoint(), "audit", "verify"],
        cwd=workspace,
        env=env,
        timeout=30,
    )
    assert verify_proc.returncode == 0, (
        f"bernstein audit verify failed:\n--- stdout ---\n{verify_proc.stdout}\n--- stderr ---\n{verify_proc.stderr}"
    )


def test_smoke_workspace_is_initialised(smoke_workspace: Path) -> None:
    """Sanity check: the fixture itself produces a usable workspace.

    This test does NOT depend on the auth fix -- it verifies that the
    test fixture's preconditions (``bernstein init`` + git repo + plan
    file) work, so a failure of the main regression test is unambiguously
    a bug in the orchestrator and not in the fixture.
    """
    workspace = smoke_workspace
    assert (workspace / ".sdd").is_dir()
    assert (workspace / "bernstein.yaml").is_file()
    # Confirm the bernstein binary itself responds at all.
    proc = _run(
        [*_bernstein_entrypoint(), "--version"],
        cwd=workspace,
        env=_smoke_env(workspace),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # And confirm git tooling is present (the run will need it).
    assert shutil.which("git") is not None
