"""Integration tests for lineage v2 (issue #1249).

End-to-end scenarios exercising the two-layer storage pattern:

  1. Orchestrator-like writer pushes a multi-event child run.
  2. Replay across multiple tasks reconstructs the full timeline.
  3. Verify catches single-byte tamper in the parent layer.
  4. Verify catches single-byte tamper in a detached child layer.
  5. Replay survives concurrent appends from multiple writers.
  6. Sigstore export round-trips through json.dumps/loads.
  7. v1 and v2 coexist - v1 reader is unaffected by v2 writes.
  8. Replay handles the parent-without-child orphan case.
  9. Export jsonl is line-equivalent to the joined replay output.
 10. Cross-task interleave preserves per-task order on replay.
"""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

from bernstein.core.lineage.entry import LineageEntry
from bernstein.core.lineage.store import LineageStore
from bernstein.core.lineage.v2_store import (
    LINEAGE_V2_ENTRY_VERSION,
    ChildBody,
    LineageV2Store,
    ParentRef,
)

_KEY = b"integration-test-v2-hmac-key"


def _pref(task: str, run: str, summary: str = "s", ts: int = 0) -> ParentRef:
    return ParentRef(
        v=LINEAGE_V2_ENTRY_VERSION,
        task_id=task,
        child_run_id=run,
        parent_call_id="call",
        summary=summary,
        child_sha="sha256:" + "0" * 64,
        ts_ns=ts,
        prev_hmac="",
        hmac="",
    )


def _cbody(
    task: str,
    run: str,
    seq: int = 0,
    kind: str = "subagent.started",
    payload: dict[str, object] | None = None,
    ts: int = 0,
) -> ChildBody:
    return ChildBody(
        v=LINEAGE_V2_ENTRY_VERSION,
        task_id=task,
        child_run_id=run,
        seq=seq,
        kind=kind,
        payload=dict(payload or {}),
        ts_ns=ts,
    )


# ---------------------------------------------------------------------------
# 1. Orchestrator-like writer pushes a multi-event child run
# ---------------------------------------------------------------------------


def test_integration_multi_event_child_run(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    child_sha, _ = store.append(
        _pref("task-A", "run-1", "spawned worker", ts=1),
        _cbody("task-A", "run-1", seq=0, kind="subagent.started", ts=1),
    )
    store.append_child_body(
        child_sha,
        _cbody("task-A", "run-1", seq=1, kind="subagent.progress", payload={"step": "compile"}, ts=2),
    )
    store.append_child_body(
        child_sha,
        _cbody("task-A", "run-1", seq=2, kind="subagent.completed", payload={"status": "ok"}, ts=3),
    )

    timeline = store.replay("task-A")
    assert len(timeline) == 1
    ref, bodies = timeline[0]
    assert ref.summary == "spawned worker"
    assert [b.kind for b in bodies] == ["subagent.started", "subagent.progress", "subagent.completed"]
    assert store.verify().ok


# ---------------------------------------------------------------------------
# 2. Replay across multiple tasks
# ---------------------------------------------------------------------------


def test_integration_multi_task_replay(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    for tid in ("T-alpha", "T-beta", "T-gamma"):
        for j in range(3):
            store.append(
                _pref(tid, f"{tid}-r{j}", f"step {j}", ts=j),
                _cbody(tid, f"{tid}-r{j}", seq=0, payload={"j": j}, ts=j),
            )

    for tid in ("T-alpha", "T-beta", "T-gamma"):
        timeline = store.replay(tid)
        assert len(timeline) == 3
        assert all(r.task_id == tid for r, _ in timeline)
    assert store.verify().ok


# ---------------------------------------------------------------------------
# 3. Tamper detection - parent layer
# ---------------------------------------------------------------------------


def test_integration_tamper_parent_detected(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    store.append(_pref("T", "r1"), _cbody("T", "r1"))
    store.append(_pref("T", "r2"), _cbody("T", "r2"))

    lines = store.parent_log.read_text().splitlines()
    obj = json.loads(lines[0])
    obj["summary"] = "REWRITTEN-BY-ATTACKER"
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    store.parent_log.write_text("\n".join(lines) + "\n")
    r = store.verify()
    assert not r.ok


# ---------------------------------------------------------------------------
# 4. Tamper detection - detached child layer
# ---------------------------------------------------------------------------


def test_integration_tamper_child_detected(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    child_sha, _ = store.append(_pref("T", "r1"), _cbody("T", "r1", payload={"v": 1}))
    cpath = store.child_log(child_sha)
    obj = json.loads(cpath.read_text().strip())
    obj["payload"] = {"v": 999}
    cpath.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")
    r = store.verify()
    assert not r.ok


# ---------------------------------------------------------------------------
# 5. Concurrent appends from multiple processes
# ---------------------------------------------------------------------------


def _proc_worker(root: str, marker: str, n: int, key: bytes) -> None:  # pragma: no cover - subproc
    from bernstein.core.lineage.v2_store import (
        LINEAGE_V2_ENTRY_VERSION,
        ChildBody,
        LineageV2Store,
        ParentRef,
    )

    store = LineageV2Store(Path(root), hmac_key=key)
    for i in range(n):
        pref = ParentRef(
            v=LINEAGE_V2_ENTRY_VERSION,
            task_id=f"T-{marker}",
            child_run_id=f"{marker}-{i}",
            parent_call_id="call",
            summary=f"{marker} step {i}",
            child_sha="sha256:" + "0" * 64,
            ts_ns=i,
            prev_hmac="",
            hmac="",
        )
        cb = ChildBody(
            v=LINEAGE_V2_ENTRY_VERSION,
            task_id=f"T-{marker}",
            child_run_id=f"{marker}-{i}",
            seq=0,
            kind="subagent.started",
            payload={"i": i},
            ts_ns=i,
        )
        store.append(pref, cb)


def test_integration_concurrent_multi_writer(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    root.mkdir()
    n_each = 10
    procs = [
        mp.get_context("spawn").Process(target=_proc_worker, args=(str(root), m, n_each, _KEY))
        for m in ("alpha", "beta", "gamma")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    store = LineageV2Store(root, hmac_key=_KEY)
    r = store.verify()
    assert r.ok
    assert r.parent_count == 3 * n_each


# ---------------------------------------------------------------------------
# 6. Sigstore export roundtrip
# ---------------------------------------------------------------------------


def test_integration_sigstore_export_roundtrip(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    for i in range(4):
        store.append(
            _pref("T", f"r{i}", f"step {i}", ts=i),
            _cbody("T", f"r{i}", payload={"i": i}, ts=i),
        )
    atts = store.export_sigstore("T")
    blob = json.dumps(atts)
    decoded = json.loads(blob)
    assert decoded == atts
    assert len(decoded) == 4
    for a in decoded:
        assert a["predicateType"] == "https://slsa.dev/provenance/v0.3"


# ---------------------------------------------------------------------------
# 7. v1 and v2 coexist
# ---------------------------------------------------------------------------


def test_integration_v1_and_v2_coexist(tmp_path: Path) -> None:
    # v1 store sits at .sdd/lineage/, v2 at .sdd/lineage/v2/.
    base = tmp_path / "lineage"
    v1 = LineageStore(base)
    v2 = LineageV2Store(base / "v2", hmac_key=_KEY)

    # v1 write.
    v1.append(
        LineageEntry(
            v=1,
            artefact_path="src/x.py",
            artefact_kind="file",
            content_hash="sha256:" + "a" * 64,
            parent_hashes=[],
            agent_id="agent:worker",
            agent_card_kid="kid-1",
            tool_call_id="tc",
            span_id="sp",
            ts_ns=1,
            operator_hmac="deadbeef" * 8,
        ),
        jws="dummy",
    )

    # v2 write.
    v2.append(_pref("T", "r1"), _cbody("T", "r1"))

    # Each is self-consistent.
    assert v2.verify().ok
    v1_records = list(v1.read_log())
    assert len(v1_records) == 1


# ---------------------------------------------------------------------------
# 8. Parent-without-child orphan handling
# ---------------------------------------------------------------------------


def test_integration_parent_without_child(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    child_sha, _ = store.append(_pref("T", "r1"), _cbody("T", "r1"))
    store.child_log(child_sha).unlink()
    # replay still surfaces the parent; verify reports failure.
    timeline = store.replay("T")
    assert len(timeline) == 1
    _, bodies = timeline[0]
    assert bodies == []
    r = store.verify()
    assert not r.ok
    assert any("missing child file" in f for f in r.failures)


# ---------------------------------------------------------------------------
# 9. Export jsonl line-equivalent to joined replay output
# ---------------------------------------------------------------------------


def test_integration_export_jsonl_matches_replay(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    child_sha, _ = store.append(_pref("T", "r1"), _cbody("T", "r1", seq=0))
    store.append_child_body(child_sha, _cbody("T", "r1", seq=1, kind="subagent.completed"))
    store.append(_pref("T", "r2"), _cbody("T", "r2"))

    text = store.export_jsonl("T")
    lines = text.strip().split("\n")
    timeline = store.replay("T")
    total = sum(1 + len(bodies) for _, bodies in timeline)
    assert len(lines) == total

    # First line is the first parent.
    head = json.loads(lines[0])
    assert head["_kind"] == "parent"
    assert head["child_run_id"] == "r1"


# ---------------------------------------------------------------------------
# 10. Cross-task interleave preserves per-task order
# ---------------------------------------------------------------------------


def test_integration_cross_task_interleave(tmp_path: Path) -> None:
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    interleaved_order = ["A1", "B1", "A2", "B2", "A3", "C1", "B3"]
    for i, label in enumerate(interleaved_order):
        task = label[0]
        run = label
        store.append(_pref(f"T-{task}", run, ts=i), _cbody(f"T-{task}", run, ts=i, payload={"i": i}))

    a = [r.child_run_id for r, _ in store.replay("T-A")]
    b = [r.child_run_id for r, _ in store.replay("T-B")]
    c = [r.child_run_id for r, _ in store.replay("T-C")]
    assert a == ["A1", "A2", "A3"]
    assert b == ["B1", "B2", "B3"]
    assert c == ["C1"]
    assert store.verify().ok


# ---------------------------------------------------------------------------
# Sigstore snapshot test (stable shape)
# ---------------------------------------------------------------------------


def test_integration_sigstore_snapshot_shape(tmp_path: Path) -> None:
    """Anchor the public sigstore export shape so accidental drifts fail loudly."""
    store = LineageV2Store(tmp_path / "v2", hmac_key=_KEY)
    store.append(
        _pref("task-fixed", "run-fixed", "snap-summary", ts=1_700_000_000_000_000_000),
        _cbody(
            "task-fixed",
            "run-fixed",
            seq=0,
            kind="subagent.started",
            payload={"fixed": True},
            ts=1_700_000_000_000_000_000,
        ),
    )
    atts = store.export_sigstore("task-fixed")
    assert len(atts) == 1
    att = atts[0]
    # Required envelope fields.
    assert att["_type"] == "https://in-toto.io/Statement/v1"
    assert att["predicateType"] == "https://slsa.dev/provenance/v0.3"
    # Required subject shape.
    assert "subject" in att
    assert isinstance(att["subject"], list)
    assert att["subject"][0]["name"].startswith("bernstein-lineage-v2/task-fixed/")
    assert len(att["subject"][0]["digest"]["sha256"]) == 64
    # Required predicate sections.
    pred = att["predicate"]
    assert pred["buildDefinition"]["buildType"] == "https://bernstein.dev/lineage/v2"
    ext = pred["buildDefinition"]["externalParameters"]
    assert ext == {
        "task_id": "task-fixed",
        "child_run_id": "run-fixed",
        "parent_call_id": "call",
        "summary": "snap-summary",
    }
    assert pred["buildDefinition"]["internalParameters"] == {"lineage_version": 2}
    rd = pred["runDetails"]
    assert rd["builder"]["id"] == "https://bernstein.dev/runners/lineage-v2"
    assert rd["metadata"]["invocationId"] == "run-fixed"
    assert rd["metadata"]["startedOn"].endswith("Z")
    assert len(rd["byproducts"]) == 1
