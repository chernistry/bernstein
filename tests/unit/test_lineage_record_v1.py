"""Schema-v1 robustness tests for the per-artifact lineage trail.

These complement :mod:`tests.unit.test_lineage_record` by covering the
v1-specific failure modes the audit surfaced:

* a torn write that leaves ``output_artifact`` missing must NOT crash
  the iterator — a long chain with one bad row should still walk the
  remaining records;
* malformed artifact dicts must degrade to an empty :class:`ArtifactRef`,
  not a ``KeyError``;
* lookups against a chain that mixes v1 and v2 records must still
  honour the v1 path/line filter.

We deliberately avoid the schema-v2 fields (``regulatory_class``,
``customer_signature``) here — those live in the regulatory-lineage
verification suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.persistence.lineage import (
    LINEAGE_DECISION_TYPE,
    SCHEMA_VERSION_V1,
    AgentRef,
    ArtifactRef,
    LineageReader,
    LineageRecord,
    LineageWriter,
    _artifact_from_dict,
    _record_from_wal,
)
from bernstein.core.persistence.wal import WALReader, WALWriter


def _sdd(tmp_path: Path) -> Path:
    sdd = tmp_path / ".sdd"
    sdd.mkdir(parents=True, exist_ok=True)
    return sdd


def _make_v1_record(path: str = "src/foo.py") -> LineageRecord:
    return LineageRecord(
        output_artifact=ArtifactRef(
            path=path,
            sha256="a" * 64,
            line_start=1,
            line_end=10,
        ),
        inputs=[ArtifactRef(path="src/bar.py", sha256="b" * 64)],
        producer=AgentRef(agent_id="agent-1", run_id="run-1"),
        prompt_sha="c" * 64,
        model="claude-sonnet",
        cost_usd=0.01,
        tokens=1000,
        timestamp=1700000000.0,
        # Explicitly v1 — leave regulatory_class / customer_signature unset.
        schema_version=SCHEMA_VERSION_V1,
    )


# ---------------------------------------------------------------------------
# _artifact_from_dict resilience
# ---------------------------------------------------------------------------


class TestArtifactFromDict:
    def test_complete_dict_round_trips(self) -> None:
        ref = _artifact_from_dict(
            {"path": "x.py", "sha256": "abc", "line_start": 5, "line_end": 10}
        )
        assert ref.path == "x.py"
        assert ref.sha256 == "abc"
        assert ref.line_start == 5
        assert ref.line_end == 10

    def test_missing_path_yields_empty(self) -> None:
        ref = _artifact_from_dict({"sha256": "abc"})
        assert ref.path == ""
        assert ref.sha256 == "abc"

    def test_missing_sha_yields_empty(self) -> None:
        ref = _artifact_from_dict({"path": "x.py"})
        assert ref.path == "x.py"
        assert ref.sha256 == ""

    def test_completely_empty_dict_yields_empty_ref(self) -> None:
        ref = _artifact_from_dict({})
        assert ref.path == ""
        assert ref.sha256 == ""
        assert ref.line_start is None
        assert ref.line_end is None

    def test_non_dict_input_returns_empty_ref(self) -> None:
        # Defensive: a corrupt WAL row could yield a list or scalar.
        assert _artifact_from_dict([]).path == ""  # type: ignore[arg-type]
        assert _artifact_from_dict("oops").sha256 == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _record_from_wal resilience
# ---------------------------------------------------------------------------


class TestRecordFromWalResilience:
    def test_v1_record_with_missing_output_artifact_does_not_crash(self) -> None:
        # Pre-fix: this raised KeyError('path').
        record = _record_from_wal({"inputs": []}, {}, ts=1.0)
        assert record.output_artifact.path == ""
        assert record.schema_version == SCHEMA_VERSION_V1

    def test_v1_record_with_partial_output_artifact(self) -> None:
        record = _record_from_wal(
            {"inputs": [], "producer": {"agent_id": "a", "run_id": "r"}},
            {"output_artifact": {"sha256": "deadbeef"}, "cost_usd": 0.5},
            ts=1700000000.0,
        )
        assert record.output_artifact.path == ""
        assert record.output_artifact.sha256 == "deadbeef"
        assert record.cost_usd == 0.5
        assert record.producer.agent_id == "a"

    def test_inputs_with_partial_artifact_dicts_do_not_crash(self) -> None:
        record = _record_from_wal(
            {
                "inputs": [
                    {"path": "src/a.py", "sha256": "aa"},
                    {},  # totally empty
                    {"sha256": "bb"},  # missing path
                ],
                "producer": {"agent_id": "a", "run_id": "r"},
            },
            {"output_artifact": {"path": "src/o.py", "sha256": "oo"}},
            ts=1.0,
        )
        assert len(record.inputs) == 3
        assert record.inputs[1].path == ""
        assert record.inputs[2].path == ""
        assert record.inputs[2].sha256 == "bb"


# ---------------------------------------------------------------------------
# LineageReader against a WAL with one bad row mixed into a long chain
# ---------------------------------------------------------------------------


class TestReaderToleratesBadRows:
    def test_iter_records_skips_torn_row_keeps_walking(self, tmp_path: Path) -> None:
        sdd = _sdd(tmp_path)
        # Append two clean records, then forge a third entry directly to
        # the WAL with a missing ``output_artifact`` payload, then a clean
        # fourth one.
        wal_writer = WALWriter(run_id="run-1", sdd_dir=sdd)
        lineage = LineageWriter(wal_writer)

        lineage.emit(_make_v1_record(path="src/a.py"))
        lineage.emit(_make_v1_record(path="src/b.py"))

        # Forged "torn" record: ``output_artifact`` field absent in output.
        wal_writer.append(
            decision_type=LINEAGE_DECISION_TYPE,
            inputs={"inputs": [], "producer": {"agent_id": "agent-2", "run_id": "run-1"}},
            output={"cost_usd": 0.0, "tokens": 0},  # no output_artifact!
            actor="agent-2",
        )

        lineage.emit(_make_v1_record(path="src/c.py"))

        reader = LineageReader(sdd)
        all_records = list(reader.iter_records(run_id="run-1"))
        # Pre-fix the torn row would crash before yielding c.py.
        paths = [r.output_artifact.path for r in all_records]
        assert paths == ["src/a.py", "src/b.py", "", "src/c.py"]

    def test_lookup_path_filter_ignores_torn_rows(self, tmp_path: Path) -> None:
        sdd = _sdd(tmp_path)
        wal_writer = WALWriter(run_id="run-2", sdd_dir=sdd)
        lineage = LineageWriter(wal_writer)
        lineage.emit(_make_v1_record(path="src/x.py"))
        # Torn row: empty output dict.
        wal_writer.append(
            decision_type=LINEAGE_DECISION_TYPE,
            inputs={"inputs": []},
            output={},
            actor="agent-2",
        )
        lineage.emit(_make_v1_record(path="src/y.py"))

        reader = LineageReader(sdd)
        x_rows = reader.lookup("src/x.py")
        y_rows = reader.lookup("src/y.py")
        assert len(x_rows) == 1
        assert len(y_rows) == 1
        # The torn row gets ArtifactRef(path="") and so doesn't match x/y.
        assert x_rows[0].output_artifact.path == "src/x.py"


# ---------------------------------------------------------------------------
# v1 round-trip via WAL preserves byte-for-byte serialization shape
# ---------------------------------------------------------------------------


class TestV1OnDiskShape:
    def test_v1_record_serialised_with_optional_fields_omitted(self, tmp_path: Path) -> None:
        sdd = _sdd(tmp_path)
        writer = LineageWriter.for_run("run-3", sdd)
        # Emit a record marked v1 — optional v2 fields stay None.
        writer.emit(_make_v1_record())

        wal_path = sdd / "runtime" / "wal" / "run-3.wal.jsonl"
        line = wal_path.read_text().splitlines()[0]
        data = json.loads(line)
        output = data["output"]
        # Optional v2 fields must not bloat v1 records on disk.
        assert "regulatory_class" not in output
        assert "customer_signature" not in output

    def test_v1_record_replay_yields_identical_output_dict(self, tmp_path: Path) -> None:
        """Two writes of the same v1 record yield identical serialised lines.

        This is the crux of replay determinism for the lineage trail:
        identical inputs must produce identical bytes on disk so a
        downstream verifier can compare two runs directly.
        """
        sdd_a = _sdd(tmp_path / "a")
        sdd_b = _sdd(tmp_path / "b")
        rec = _make_v1_record(path="src/det.py")

        LineageWriter.for_run("run-x", sdd_a).emit(rec)
        LineageWriter.for_run("run-x", sdd_b).emit(rec)

        line_a = (sdd_a / "runtime" / "wal" / "run-x.wal.jsonl").read_text().splitlines()[0]
        line_b = (sdd_b / "runtime" / "wal" / "run-x.wal.jsonl").read_text().splitlines()[0]
        # Strip the timestamp field which is set by the WAL writer to
        # ``time.time()`` — the rest of the payload must match exactly.
        d_a = json.loads(line_a)
        d_b = json.loads(line_b)
        for d in (d_a, d_b):
            d.pop("timestamp", None)
            d.pop("entry_hash", None)
            d.pop("prev_hash", None)
            d["output"].pop("timestamp", None)
        assert d_a == d_b


# ---------------------------------------------------------------------------
# Reader + writer round-trip with malformed input fields
# ---------------------------------------------------------------------------


class TestEndToEndResilience:
    def test_chain_verification_passes_with_torn_row(self, tmp_path: Path) -> None:
        """The WAL hash chain remains intact even when one row is torn.

        ``output_artifact`` may be missing from the payload, but the WAL
        hashing happens over the *whole* JSON line — so the chain stays
        verifiable. Only the lineage decoder needs to tolerate the gap.
        """
        sdd = _sdd(tmp_path)
        wal_writer = WALWriter(run_id="run-c", sdd_dir=sdd)
        lineage = LineageWriter(wal_writer)
        lineage.emit(_make_v1_record(path="src/z.py"))
        wal_writer.append(
            decision_type=LINEAGE_DECISION_TYPE,
            inputs={"inputs": []},
            output={},
            actor="agent",
        )
        lineage.emit(_make_v1_record(path="src/zz.py"))

        reader = WALReader(run_id="run-c", sdd_dir=sdd)
        ok, errors = reader.verify_chain()
        assert ok, errors


if __name__ == "__main__":
    pytest.main([__file__, "-x", "-q"])
