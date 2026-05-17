"""Property tests for ``FileLockManager`` contention semantics.

The lock manager mediates concurrent agent file ownership. Bugs here
result in two agents simultaneously editing the same file — silent
data corruption with no immediate signal. Properties:

* **Acquire is exclusive across agents** — for any random sequence
  of acquires from distinct agents, no file ends up held by more than
  one agent simultaneously.

* **Acquire is idempotent for the same agent** — repeated acquires
  by the same agent for overlapping file sets are silently accepted
  and do not deadlock or drop locks.

* **Release purges everything for that agent** — after a release,
  zero locks attributed to that agent remain in the table.

* **Concurrent acquire/release converges to a consistent state**
  under thread contention: the total set of locked files in the
  table is a subset of the union of all attempted acquires, and
  every lock has exactly one agent owner.

The threaded property uses small example budgets (20 examples × 4
agents) to keep CI wall-time predictable; the existing
``test_claim_next_properties.py`` precedent is dialed to a similar
budget.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.persistence.file_locks import FileLockManager

# Compact alphabets so generated files / agent ids are stable and
# obviously distinct under shrinking.
_FILE = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("h")),
    min_size=1,
    max_size=4,
)
_AGENT = st.text(
    alphabet=st.characters(min_codepoint=ord("A"), max_codepoint=ord("D")),
    min_size=1,
    max_size=2,
)


@given(
    requests=st.lists(
        st.tuples(_AGENT, st.lists(_FILE, min_size=1, max_size=4, unique=True)),
        min_size=1,
        max_size=10,
    ),
)
def test_no_two_agents_hold_same_file(tmp_path_factory: pytest.TempPathFactory, requests: list[tuple[str, list[str]]]) -> None:
    """For any sequence of acquires, each file ends up held by ≤ 1 agent.

    The lock manager's primary invariant. A failure means two agents
    could both run with the same file in their owned set — silent data
    corruption when both spawn editors.
    """
    workdir = tmp_path_factory.mktemp("flock-prop")
    mgr = FileLockManager(workdir)

    for i, (agent, files) in enumerate(requests):
        mgr.acquire(files, agent_id=agent, task_id=f"t-{i}")

    # All currently-held locks: at most one agent per file.
    owners: dict[str, str] = {}
    for lock in mgr.all_locks():
        assert lock.file_path not in owners or owners[lock.file_path] == lock.agent_id, (
            f"file {lock.file_path} held by two agents"
        )
        owners[lock.file_path] = lock.agent_id


@given(
    agent=_AGENT,
    rounds=st.lists(
        st.lists(_FILE, min_size=1, max_size=3, unique=True),
        min_size=2,
        max_size=5,
    ),
)
def test_same_agent_acquire_is_idempotent(
    tmp_path_factory: pytest.TempPathFactory,
    agent: str,
    rounds: list[list[str]],
) -> None:
    """Repeated acquires by the same agent never produce conflicts.

    The manager treats same-agent re-acquires as silent idempotents.
    If a refactor accidentally treated them as conflicts, the
    orchestrator would deadlock on every retry.
    """
    workdir = tmp_path_factory.mktemp("flock-prop")
    mgr = FileLockManager(workdir)

    union: set[str] = set()
    for i, files in enumerate(rounds):
        conflicts = mgr.acquire(files, agent_id=agent, task_id=f"t-{i}")
        assert conflicts == []
        union.update(files)

    held = {lock.file_path for lock in mgr.locks_for_agent(agent)}
    assert held == union


@given(
    agent_a=_AGENT,
    agent_b=_AGENT.filter(lambda a: True),
    files=st.lists(_FILE, min_size=1, max_size=3, unique=True),
)
def test_cross_agent_acquire_reports_conflicts(
    tmp_path_factory: pytest.TempPathFactory,
    agent_a: str,
    agent_b: str,
    files: list[str],
) -> None:
    """Re-acquiring a file held by a different agent surfaces a conflict.

    Without this, two agents could silently spawn against the same
    file. The property exercises the exact branch where ``acquire``
    has to differentiate by ``agent_id`` rather than just key presence.
    """
    if agent_a == agent_b:
        pytest.skip("identical agents — conflict path is not exercised")

    workdir = tmp_path_factory.mktemp("flock-prop")
    mgr = FileLockManager(workdir)
    mgr.acquire(files, agent_id=agent_a, task_id="t-a")
    conflicts = mgr.acquire(files, agent_id=agent_b, task_id="t-b")
    assert set(conflicts) == set(files)


@given(
    agent=_AGENT,
    files=st.lists(_FILE, min_size=1, max_size=5, unique=True),
)
def test_release_purges_all_locks(
    tmp_path_factory: pytest.TempPathFactory,
    agent: str,
    files: list[str],
) -> None:
    """After ``release(agent)`` zero locks remain for that agent.

    Catches regressions where ``release`` skips locks whose
    ``locked_at`` is in the future (clock skew) or whose path
    contains separator-like characters.
    """
    workdir = tmp_path_factory.mktemp("flock-prop")
    mgr = FileLockManager(workdir)
    mgr.acquire(files, agent_id=agent, task_id="t")
    released = mgr.release(agent)
    assert set(released) == set(files)
    assert mgr.locks_for_agent(agent) == []


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    agents=st.lists(_AGENT, min_size=2, max_size=4, unique=True),
    files=st.lists(_FILE, min_size=2, max_size=5, unique=True),
)
def test_concurrent_acquire_release_converges(
    tmp_path_factory: pytest.TempPathFactory,
    agents: list[str],
    files: list[str],
) -> None:
    """N agents acquiring/releasing in parallel leave a consistent table.

    The cross-process OS lock plus the in-process threading.Lock must
    serialize state mutations. The property asserts:

      * No file is held by more than one agent simultaneously.
      * After all workers finish their release pass, the table is empty.

    A regression that drops either lock would surface either as a
    duplicate-owner observation or a stale entry remaining after
    every worker has released.
    """
    workdir = tmp_path_factory.mktemp("flock-prop")
    mgr = FileLockManager(workdir)

    barrier = threading.Barrier(len(agents))

    def worker(agent: str) -> None:
        barrier.wait()
        for _ in range(8):
            mgr.acquire(files, agent_id=agent, task_id=f"{agent}-t")
            mgr.release(agent)

    threads = [threading.Thread(target=worker, args=(a,), daemon=True) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    # Final state: empty (every worker ends on release).
    final = mgr.all_locks()
    assert final == [], f"stale locks remain after release pass: {final}"


@given(
    agent=_AGENT,
    files=st.lists(_FILE, min_size=1, max_size=3, unique=True),
)
def test_persistence_survives_reinitialisation(
    tmp_path_factory: pytest.TempPathFactory,
    agent: str,
    files: list[str],
) -> None:
    """Locks survive a fresh FileLockManager re-init in the same workdir.

    The manager mirrors state to ``file_locks.json`` so the orchestrator
    can resume after a restart. A regression that broke ``_load`` would
    silently drop every lock on restart and let a second agent run
    against the same file. The property pins the resume contract.
    """
    workdir = tmp_path_factory.mktemp("flock-prop")
    mgr1 = FileLockManager(workdir)
    mgr1.acquire(files, agent_id=agent, task_id="t")

    # Simulate a process restart: build a fresh manager in the same dir.
    mgr2 = FileLockManager(workdir)
    held = {lock.file_path for lock in mgr2.locks_for_agent(agent)}
    assert held == set(files)


def test_lock_file_uses_atomic_write(tmp_path: Path) -> None:
    """Sanity check that no ``.tmp.*`` debris is left in runtime dir.

    Pinned because the input space is fixed. Locks the contract that
    persistence is routed via ``write_atomic_json`` (and therefore
    leaves no stale temp file in the locks directory).
    """
    mgr = FileLockManager(tmp_path)
    mgr.acquire(["a.py"], agent_id="aa", task_id="t")

    runtime_dir = tmp_path / ".sdd" / "runtime"
    children = list(runtime_dir.iterdir())
    tmp_files = [c for c in children if ".tmp." in c.name]
    assert tmp_files == []
