"""Unit tests for :mod:`bernstein.core.observability.decision_log`.

These tests are the load-bearing contract for the v1 decision-log
schema: every field, every guard, every truncation rule. They run
quickly (no network, no fixtures heavier than a temp dir) and are
the first thing CI runs before the integration suite.

Coverage targets:

* schema-version invariant + migration hook surface
* every required and optional field round-trips through
  ``to_dict`` / ``from_dict``
* validation rules (kind, confidence range, chosen non-empty)
* malformed JSONL lines are skipped (not raised)
* alternatives truncation at :data:`MAX_ALTERNATIVES`
* rationale truncation at :data:`MAX_RATIONALE_LEN`
* concurrent appends from multiple threads do not interleave bytes
* env-var disable flag turns the writer into a no-op
* duration parser accepts s/m/h/d + bare seconds
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from bernstein.core.observability import decision_log as dl

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_ledger(tmp_path: Path) -> Path:
    """Yield a temp path the writer should target."""
    return tmp_path / "decisions.jsonl"


@pytest.fixture(autouse=True)
def _enable_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force-enable the writer for every test (defaults are on anyway).

    Defensive against developer shells that have BERNSTEIN_DECISION_LOG=0
    exported globally - otherwise the whole suite silently passes by
    writing nothing.
    """
    monkeypatch.delenv(dl.ENV_DISABLE, raising=False)


# ---------------------------------------------------------------------------
# Constants / metadata
# ---------------------------------------------------------------------------


def test_schema_version_is_one() -> None:
    """The schema version is pinned at 1 in v1; bumping needs a migration."""
    assert dl.SCHEMA_VERSION == 1


def test_valid_kinds_closed_set() -> None:
    """v1+autoheal+tier3 vocabulary is exactly these eight kinds."""
    assert (
        frozenset(
            {
                "model_route",
                "mode_profile",
                "criterion_profile",
                "gate_fire",
                "autoheal_strategy",
                "tier3_shadow",
                "cordon_violation",
                "recurrence_escalation",
            }
        )
        == dl.VALID_KINDS
    )


def test_default_path_under_sdd_runtime() -> None:
    """Default file lives under ``.sdd/runtime/`` as required by the issue."""
    assert Path(".sdd/runtime/decisions.jsonl") == dl.DEFAULT_PATH


def test_env_disable_constant() -> None:
    """The env var name is the public guard documented in the module."""
    assert dl.ENV_DISABLE == "BERNSTEIN_DECISION_LOG"


def test_max_alternatives_positive() -> None:
    """The cap must be a positive int - zero would suppress all alternatives."""
    assert dl.MAX_ALTERNATIVES > 0


def test_max_rationale_len_positive() -> None:
    """The rationale cap must be a positive int."""
    assert dl.MAX_RATIONALE_LEN > 0


# ---------------------------------------------------------------------------
# new_decision_id
# ---------------------------------------------------------------------------


def test_new_decision_id_is_unique() -> None:
    """Two consecutive calls produce different ids (uuid4 collision check)."""
    a = dl.new_decision_id()
    b = dl.new_decision_id()
    assert a != b


def test_new_decision_id_has_prefix() -> None:
    """All decision ids start with ``dec-`` for easy log-grep."""
    assert dl.new_decision_id().startswith("dec-")


def test_new_decision_id_uuid_body_hex() -> None:
    """The body after ``dec-`` is hex (uuid4 hex form)."""
    body = dl.new_decision_id()[4:]
    int(body, 16)  # must not raise
    assert len(body) == 32


# ---------------------------------------------------------------------------
# Alternative dataclass
# ---------------------------------------------------------------------------


def test_alternative_to_dict_round_trip() -> None:
    """Alternative survives a dict round trip with no field loss."""
    alt = dl.Alternative(id="m-1", score=0.42, reason="too slow")
    assert dl.Alternative.from_dict(alt.to_dict()) == alt


def test_alternative_defaults() -> None:
    """Score defaults to 0.0, reason defaults to empty string."""
    alt = dl.Alternative(id="m-1")
    assert alt.score == 0.0
    assert alt.reason == ""


def test_alternative_from_dict_tolerates_missing_keys() -> None:
    """Missing optional keys yield default values, not KeyError."""
    alt = dl.Alternative.from_dict({"id": "m-1"})
    assert alt == dl.Alternative(id="m-1")


def test_alternative_from_dict_coerces_score_to_float() -> None:
    """Int scores are coerced to float so downstream maths is uniform."""
    alt = dl.Alternative.from_dict({"id": "m-1", "score": 1})
    assert isinstance(alt.score, float)


# ---------------------------------------------------------------------------
# DecisionRecord
# ---------------------------------------------------------------------------


def _sample_record() -> dl.DecisionRecord:
    return dl.DecisionRecord(
        ts=1700000000.0,
        decision_id="dec-deadbeef",
        kind="model_route",
        chosen="claude-sonnet-4.7",
        alternatives=(dl.Alternative(id="claude-haiku", score=0.1, reason="too weak"),),
        confidence=0.9,
        rationale="role=manager prefers high-quality model",
        parent_decision_id=None,
        policy_path=("route_task", "bandit"),
        winner_score=0.92,
        inputs={"task_id": "t-1"},
    )


def test_decision_record_round_trip() -> None:
    """Round trip through dict survives every field, including nested ones."""
    rec = _sample_record()
    again = dl.DecisionRecord.from_dict(rec.to_dict())
    assert again == rec


def test_decision_record_to_dict_field_order() -> None:
    """Field order is stable so downstream snapshot tests stay green."""
    rec = _sample_record()
    keys = list(rec.to_dict().keys())
    assert keys[:4] == ["schema_version", "ts", "decision_id", "kind"]


def test_decision_record_from_dict_missing_ts() -> None:
    """Missing ``ts`` is a programmer error -> ValueError."""
    with pytest.raises(ValueError, match="missing required field: ts"):
        dl.DecisionRecord.from_dict({"decision_id": "x", "kind": "model_route", "chosen": "m"})


def test_decision_record_from_dict_missing_decision_id() -> None:
    """Missing ``decision_id`` is a programmer error."""
    with pytest.raises(ValueError, match="missing required field: decision_id"):
        dl.DecisionRecord.from_dict({"ts": 1.0, "kind": "model_route", "chosen": "m"})


def test_decision_record_from_dict_missing_kind() -> None:
    """Missing ``kind`` is a programmer error."""
    with pytest.raises(ValueError, match="missing required field: kind"):
        dl.DecisionRecord.from_dict({"ts": 1.0, "decision_id": "x", "chosen": "m"})


def test_decision_record_from_dict_missing_chosen() -> None:
    """Missing ``chosen`` is a programmer error."""
    with pytest.raises(ValueError, match="missing required field: chosen"):
        dl.DecisionRecord.from_dict({"ts": 1.0, "decision_id": "x", "kind": "model_route"})


def test_decision_record_from_dict_unknown_kind() -> None:
    """Unknown kinds are rejected to keep the v1 vocabulary closed."""
    with pytest.raises(ValueError, match="unknown decision kind"):
        dl.DecisionRecord.from_dict(
            {
                "ts": 1.0,
                "decision_id": "x",
                "kind": "spaceship_navigation",
                "chosen": "m",
            }
        )


def test_decision_record_from_dict_bad_alternatives_type() -> None:
    """``alternatives`` must be a list, not a dict/string."""
    with pytest.raises(ValueError, match="alternatives must be a list"):
        dl.DecisionRecord.from_dict(
            {
                "ts": 1.0,
                "decision_id": "x",
                "kind": "model_route",
                "chosen": "m",
                "alternatives": "not-a-list",
            }
        )


def test_decision_record_from_dict_bad_policy_path_type() -> None:
    """``policy_path`` must be a list."""
    with pytest.raises(ValueError, match="policy_path must be a list"):
        dl.DecisionRecord.from_dict(
            {
                "ts": 1.0,
                "decision_id": "x",
                "kind": "model_route",
                "chosen": "m",
                "policy_path": "not-a-list",
            }
        )


def test_decision_record_from_dict_bad_inputs_type() -> None:
    """``inputs`` must be a dict."""
    with pytest.raises(ValueError, match="inputs must be a dict"):
        dl.DecisionRecord.from_dict(
            {
                "ts": 1.0,
                "decision_id": "x",
                "kind": "model_route",
                "chosen": "m",
                "inputs": "not-a-dict",
            }
        )


def test_decision_record_alternatives_skip_non_dict() -> None:
    """Non-dict entries inside the alternatives list are silently dropped."""
    rec = dl.DecisionRecord.from_dict(
        {
            "ts": 1.0,
            "decision_id": "x",
            "kind": "model_route",
            "chosen": "m",
            "alternatives": [{"id": "a"}, "garbage", 42, {"id": "b"}],
        }
    )
    assert [a.id for a in rec.alternatives] == ["a", "b"]


def test_decision_record_defaults() -> None:
    """Optional fields default to safe empty values."""
    rec = dl.DecisionRecord(
        ts=1.0,
        decision_id="x",
        kind="model_route",
        chosen="m",
        alternatives=(),
        confidence=0.0,
        rationale="",
    )
    assert rec.parent_decision_id is None
    assert rec.policy_path == ()
    assert rec.winner_score == 0.0
    assert rec.inputs == {}
    assert rec.schema_version == dl.SCHEMA_VERSION


def test_decision_record_to_dict_serialises_alternatives() -> None:
    """Alternatives serialise as plain dicts so JSON encoders accept them."""
    rec = _sample_record()
    data = rec.to_dict()
    assert isinstance(data["alternatives"], list)
    assert isinstance(data["alternatives"][0], dict)


# ---------------------------------------------------------------------------
# record_decision (writer)
# ---------------------------------------------------------------------------


def test_record_decision_writes_one_line(tmp_ledger: Path) -> None:
    """A single write produces exactly one JSON line."""
    rec = dl.record_decision(
        kind="model_route",
        chosen="claude-sonnet-4.7",
        rationale="rt",
        confidence=0.5,
        path=tmp_ledger,
    )
    assert rec is not None
    lines = tmp_ledger.read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["chosen"] == "claude-sonnet-4.7"


def test_record_decision_disabled_returns_none(tmp_ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the env var set to 0 the writer is a no-op."""
    monkeypatch.setenv(dl.ENV_DISABLE, "0")
    rec = dl.record_decision(kind="model_route", chosen="m", path=tmp_ledger)
    assert rec is None
    assert not tmp_ledger.exists()


def test_record_decision_rejects_unknown_kind(tmp_ledger: Path) -> None:
    """Bad kinds raise before any I/O happens."""
    with pytest.raises(ValueError, match="unknown decision kind"):
        dl.record_decision(kind="bogus", chosen="m", path=tmp_ledger)
    assert not tmp_ledger.exists()


def test_record_decision_rejects_confidence_above_one(tmp_ledger: Path) -> None:
    """Confidence must be in ``[0.0, 1.0]``."""
    with pytest.raises(ValueError, match=r"confidence must be in"):
        dl.record_decision(kind="model_route", chosen="m", confidence=1.5, path=tmp_ledger)


def test_record_decision_rejects_confidence_below_zero(tmp_ledger: Path) -> None:
    """Negative confidence is nonsense - reject at the boundary."""
    with pytest.raises(ValueError, match=r"confidence must be in"):
        dl.record_decision(kind="model_route", chosen="m", confidence=-0.1, path=tmp_ledger)


def test_record_decision_rejects_empty_chosen(tmp_ledger: Path) -> None:
    """The winner must be identifiable; empty string is rejected."""
    with pytest.raises(ValueError, match="chosen must be a non-empty"):
        dl.record_decision(kind="model_route", chosen="", path=tmp_ledger)


def test_record_decision_creates_parent_dirs(tmp_path: Path) -> None:
    """Writer creates missing parent directories so callers don't have to."""
    nested = tmp_path / "a" / "b" / "c" / "decisions.jsonl"
    dl.record_decision(kind="model_route", chosen="m", path=nested)
    assert nested.exists()


def test_record_decision_appends_not_overwrites(tmp_ledger: Path) -> None:
    """Subsequent calls extend the file, never overwrite earlier records."""
    dl.record_decision(kind="model_route", chosen="m1", path=tmp_ledger)
    dl.record_decision(kind="model_route", chosen="m2", path=tmp_ledger)
    lines = tmp_ledger.read_text().splitlines()
    assert len(lines) == 2
    assert "m1" in lines[0]
    assert "m2" in lines[1]


def test_record_decision_truncates_alternatives(tmp_ledger: Path) -> None:
    """Alternatives are clamped to MAX_ALTERNATIVES at write time."""
    many = [dl.Alternative(id=f"a-{i}") for i in range(dl.MAX_ALTERNATIVES + 25)]
    rec = dl.record_decision(kind="model_route", chosen="winner", alternatives=many, path=tmp_ledger)
    assert rec is not None
    assert len(rec.alternatives) == dl.MAX_ALTERNATIVES


def test_record_decision_truncates_rationale(tmp_ledger: Path) -> None:
    """Rationale longer than MAX_RATIONALE_LEN is suffixed with an ellipsis."""
    long = "x" * (dl.MAX_RATIONALE_LEN + 500)
    rec = dl.record_decision(kind="model_route", chosen="m", rationale=long, path=tmp_ledger)
    assert rec is not None
    assert len(rec.rationale) == dl.MAX_RATIONALE_LEN
    assert rec.rationale.endswith("…")


def test_record_decision_explicit_ts(tmp_ledger: Path) -> None:
    """An explicit ``ts`` is honoured (used by replay/integration tests)."""
    rec = dl.record_decision(kind="model_route", chosen="m", ts=1234.5, path=tmp_ledger)
    assert rec is not None
    assert rec.ts == 1234.5


def test_record_decision_inputs_preserved(tmp_ledger: Path) -> None:
    """Inputs round-trip through the JSONL line."""
    dl.record_decision(
        kind="model_route",
        chosen="m",
        inputs={"task_id": "t-1", "role": "manager"},
        path=tmp_ledger,
    )
    parsed = json.loads(tmp_ledger.read_text().splitlines()[0])
    assert parsed["inputs"]["task_id"] == "t-1"
    assert parsed["inputs"]["role"] == "manager"


def test_record_decision_with_parent_link(tmp_ledger: Path) -> None:
    """Parent decision id is persisted so tree views can reconstruct it."""
    rec = dl.record_decision(
        kind="model_route",
        chosen="m",
        parent_decision_id="dec-parent",
        path=tmp_ledger,
    )
    assert rec is not None
    assert rec.parent_decision_id == "dec-parent"


def test_record_decision_policy_path_persisted(tmp_ledger: Path) -> None:
    """policy_path is captured verbatim and preserves order."""
    dl.record_decision(
        kind="model_route",
        chosen="m",
        policy_path=("a", "b", "c"),
        path=tmp_ledger,
    )
    parsed = json.loads(tmp_ledger.read_text().splitlines()[0])
    assert parsed["policy_path"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_record_decision_concurrent_appends(tmp_ledger: Path) -> None:
    """N threads producing 1 record each yield N well-formed lines."""
    n_threads = 16
    per_thread = 5
    errors: list[BaseException] = []

    def worker(idx: int) -> None:
        try:
            for j in range(per_thread):
                dl.record_decision(
                    kind="model_route",
                    chosen=f"m-{idx}-{j}",
                    path=tmp_ledger,
                )
        except BaseException as exc:  # pragma: no cover - reported via list
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    lines = tmp_ledger.read_text().splitlines()
    assert len(lines) == n_threads * per_thread
    # Every line must parse as JSON; no torn writes survived.
    for line in lines:
        json.loads(line)


# ---------------------------------------------------------------------------
# Reader / replay
# ---------------------------------------------------------------------------


def test_iter_records_empty_file_yields_nothing(tmp_ledger: Path) -> None:
    """A non-existent ledger yields no records and does not raise."""
    assert list(dl.iter_records(tmp_ledger)) == []


def test_iter_records_skips_blank_lines(tmp_ledger: Path) -> None:
    """Blank lines (legitimate after some shells redirect output) are skipped."""
    dl.record_decision(kind="model_route", chosen="m", path=tmp_ledger)
    with tmp_ledger.open("a", encoding="utf-8") as fh:
        fh.write("\n\n   \n")
    records = list(dl.iter_records(tmp_ledger))
    assert len(records) == 1


def test_iter_records_skips_malformed_lines(tmp_ledger: Path) -> None:
    """Garbage lines are skipped, not raised - replay is best-effort."""
    dl.record_decision(kind="model_route", chosen="m1", path=tmp_ledger)
    with tmp_ledger.open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
        fh.write('{"bad": "missing required fields"}\n')
    dl.record_decision(kind="model_route", chosen="m2", path=tmp_ledger)
    records = list(dl.iter_records(tmp_ledger))
    assert [r.chosen for r in records] == ["m1", "m2"]


def test_iter_records_skips_non_object_json(tmp_ledger: Path) -> None:
    """A JSON array (not object) on a line is malformed and skipped."""
    with tmp_ledger.open("w", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]\n")
        fh.write(
            json.dumps(
                {
                    "schema_version": 1,
                    "ts": 1.0,
                    "decision_id": "x",
                    "kind": "model_route",
                    "chosen": "m",
                }
            )
            + "\n"
        )
    records = list(dl.iter_records(tmp_ledger))
    assert len(records) == 1


def test_replay_preserves_write_order(tmp_ledger: Path) -> None:
    """Records come back in the order they were written."""
    for i in range(5):
        dl.record_decision(kind="model_route", chosen=f"m-{i}", path=tmp_ledger)
    records = dl.replay(tmp_ledger)
    assert [r.chosen for r in records] == [f"m-{i}" for i in range(5)]


def test_replay_ts_non_decreasing(tmp_ledger: Path) -> None:
    """For a single-process session, ``ts`` never regresses on replay."""
    for _ in range(20):
        dl.record_decision(kind="model_route", chosen="m", path=tmp_ledger)
    records = dl.replay(tmp_ledger)
    timestamps = [r.ts for r in records]
    assert timestamps == sorted(timestamps)


def test_replay_rejects_future_schema_version(tmp_ledger: Path) -> None:
    """A record claiming schema_version > current is treated as malformed."""
    with tmp_ledger.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "schema_version": 999,
                    "ts": 1.0,
                    "decision_id": "x",
                    "kind": "model_route",
                    "chosen": "m",
                }
            )
            + "\n"
        )
    # The future-version record is skipped (treated as malformed).
    records = dl.replay(tmp_ledger)
    assert records == []


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def test_filter_by_kind() -> None:
    """``filter_by_kind`` returns only records whose kind matches."""
    records = [
        dl.DecisionRecord(
            ts=1, decision_id="a", kind="model_route", chosen="m", alternatives=(), confidence=0, rationale=""
        ),
        dl.DecisionRecord(
            ts=2, decision_id="b", kind="mode_profile", chosen="p", alternatives=(), confidence=0, rationale=""
        ),
    ]
    matched = dl.filter_by_kind(records, "model_route")
    assert len(matched) == 1
    assert matched[0].decision_id == "a"


def test_filter_by_kind_no_match_returns_empty() -> None:
    """No matches → empty list, not an exception."""
    records = [
        dl.DecisionRecord(
            ts=1, decision_id="a", kind="model_route", chosen="m", alternatives=(), confidence=0, rationale=""
        ),
    ]
    assert dl.filter_by_kind(records, "gate_fire") == []


def test_filter_since_returns_only_recent() -> None:
    """``filter_since(cutoff)`` keeps records at or after cutoff."""
    records = [
        dl.DecisionRecord(
            ts=t, decision_id=f"d-{t}", kind="model_route", chosen="m", alternatives=(), confidence=0, rationale=""
        )
        for t in (10.0, 20.0, 30.0, 40.0)
    ]
    assert [r.ts for r in dl.filter_since(records, 25.0)] == [30.0, 40.0]


def test_filter_since_inclusive_boundary() -> None:
    """The cutoff is inclusive - a record at the exact cutoff is kept."""
    records = [
        dl.DecisionRecord(
            ts=10.0, decision_id="a", kind="model_route", chosen="m", alternatives=(), confidence=0, rationale=""
        ),
    ]
    assert dl.filter_since(records, 10.0) == records


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


def test_parse_duration_seconds() -> None:
    """``30s`` resolves to 30.0 seconds."""
    assert dl.parse_duration("30s") == 30.0


def test_parse_duration_minutes() -> None:
    """``5m`` resolves to 300.0 seconds."""
    assert dl.parse_duration("5m") == 300.0


def test_parse_duration_hours() -> None:
    """``2h`` resolves to 7200.0 seconds."""
    assert dl.parse_duration("2h") == 7200.0


def test_parse_duration_days() -> None:
    """``1d`` resolves to 86400.0 seconds."""
    assert dl.parse_duration("1d") == 86400.0


def test_parse_duration_bare_number_is_seconds() -> None:
    """A bare integer is interpreted as seconds for unix-ish ergonomics."""
    assert dl.parse_duration("90") == 90.0


def test_parse_duration_rejects_empty() -> None:
    """Empty input is a programmer error."""
    with pytest.raises(ValueError, match="empty duration"):
        dl.parse_duration("")


def test_parse_duration_rejects_garbage() -> None:
    """Unparseable input raises ValueError, not a silent zero."""
    with pytest.raises(ValueError):
        dl.parse_duration("two-hours")
