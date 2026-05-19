"""Tests for ``bernstein lineage conflicts`` / ``resolve`` (CliRunner)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.commands.lineage_cmd import lineage_cmd
from bernstein.core.lineage.audit import DEFAULT_AUDIT_RELPATH
from bernstein.core.lineage.entry import LineageEntry, entry_hash


def _h(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def _mk_entry(
    agent_id: str,
    artefact_path: str,
    content_hash: str,
    parent_hashes: list[str],
    ts_ns: int,
    *,
    kid: str = "k1",
) -> LineageEntry:
    return LineageEntry(
        v=1,
        artefact_path=artefact_path,
        artefact_kind="file",
        content_hash=content_hash,
        parent_hashes=parent_hashes,
        agent_id=agent_id,
        agent_card_kid=kid,
        tool_call_id="tc",
        span_id="span",
        ts_ns=ts_ns,
        operator_hmac="deadbeef" * 8,
    )


def _write_log(tmp_path: Path, entries: list[LineageEntry]) -> Path:
    log = tmp_path / "lineage" / "log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        for e in entries:
            f.write(json.dumps(asdict(e), sort_keys=True) + "\n")
    return log


# ── conflicts: list-when-empty ──────────────────────────────────────────────


def test_conflicts_list_empty_log(tmp_path: Path) -> None:
    log = tmp_path / "lineage" / "log.jsonl"
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--log", str(log)])
    assert result.exit_code == 0
    assert "No log" in result.output


def test_conflicts_list_no_forks(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    log = _write_log(tmp_path, [g])
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--log", str(log)])
    assert result.exit_code == 0
    assert "No unresolved forks" in result.output


def test_conflicts_json_empty(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    log = _write_log(tmp_path, [g])
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--log", str(log), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


# ── conflicts: list-with-one ────────────────────────────────────────────────


def test_conflicts_list_with_one(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1 = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f2 = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f1, f2])
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--log", str(log), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload) == 1
    view = payload[0]
    assert view["artefact_path"] == "x.py"
    assert {c["agent_id"] for c in view["candidates"]} == {"agent:a", "agent:b"}
    assert view["parent_hash"] == entry_hash(g)
    assert "char_count_diff" in view


def test_conflicts_human_format(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1 = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f2 = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f1, f2])
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--log", str(log)], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "unresolved fork" in result.output
    assert "x.py" in result.output
    assert "agent:a" in result.output
    assert "agent:b" in result.output


# ── conflicts: list-with-many ───────────────────────────────────────────────


def test_conflicts_list_with_many(tmp_path: Path) -> None:
    g1 = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1a = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g1)], 2)
    f1b = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g1)], 3)
    g2 = _mk_entry("agent:a", "y.py", _h("4"), [], 1)
    f2a = _mk_entry("agent:a", "y.py", _h("5"), [entry_hash(g2)], 2)
    f2b = _mk_entry("agent:b", "y.py", _h("6"), [entry_hash(g2)], 3)
    log = _write_log(tmp_path, [g1, f1a, f1b, g2, f2a, f2b])
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--log", str(log), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload) == 2
    paths = {view["artefact_path"] for view in payload}
    assert paths == {"x.py", "y.py"}


def test_conflicts_artefact_filter(tmp_path: Path) -> None:
    g1 = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1a = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g1)], 2)
    f1b = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g1)], 3)
    g2 = _mk_entry("agent:a", "y.py", _h("4"), [], 1)
    f2a = _mk_entry("agent:a", "y.py", _h("5"), [entry_hash(g2)], 2)
    f2b = _mk_entry("agent:b", "y.py", _h("6"), [entry_hash(g2)], 3)
    log = _write_log(tmp_path, [g1, f1a, f1b, g2, f2a, f2b])
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["conflicts", "--artefact", "y.py", "--log", str(log), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["artefact_path"] == "y.py"


# ── resolve: human policy ───────────────────────────────────────────────────


def _read_audit_records(sdd_dir: Path) -> list[dict[str, object]]:
    audit_path = sdd_dir / DEFAULT_AUDIT_RELPATH
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]


def test_resolve_human_interactive_pick(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1 = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f2 = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f1, f2])
    sdd_dir = tmp_path
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "human",
            "--log",
            str(log),
            "--sdd-dir",
            str(sdd_dir),
            "--reason",
            "operator picked candidate 1",
        ],
        input="1\n",
    )
    assert result.exit_code == 0, result.output
    assert "Resolved" in result.output
    records = _read_audit_records(sdd_dir)
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "lineage.merge_entry"
    assert rec["policy"] == "human"
    assert rec["artefact_path"] == "x.py"
    assert rec["reason"] == "operator picked candidate 1"


def test_resolve_human_auto_yes(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1 = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f2 = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f1, f2])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "human",
            "--yes",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output


def test_resolve_human_diff_flag_prints_diff(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1 = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f2 = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f1, f2])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "human",
            "--diff",
            "--yes",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "candidate-a" in result.output
    assert "candidate-b" in result.output


# ── resolve: first-writer ───────────────────────────────────────────────────


def test_resolve_first_writer(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    early = _mk_entry("agent:b", "x.py", _h("2"), [entry_hash(g)], 2)
    late = _mk_entry("agent:a", "x.py", _h("3"), [entry_hash(g)], 10)
    log = _write_log(tmp_path, [g, early, late])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "first-writer",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    records = _read_audit_records(tmp_path)
    assert len(records) == 1
    rec = records[0]
    assert rec["policy"] == "first-writer"
    assert rec["winner_hash"] == entry_hash(early)


# ── resolve: agent policy ───────────────────────────────────────────────────


def test_resolve_agent_policy_picks_designated(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f_a = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f_b = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f_a, f_b])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "agent:b",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    records = _read_audit_records(tmp_path)
    rec = records[0]
    assert rec["policy"] == "agent:b"
    assert rec["winner_hash"] == entry_hash(f_b)


def test_resolve_agent_policy_missing_candidate_fails(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f_a = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f_b = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f_a, f_b])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "agent:c",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1


# ── resolve: refusal when no fork ───────────────────────────────────────────


def test_resolve_refuses_without_fork(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    log = _write_log(tmp_path, [g])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "first-writer",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "No unresolved fork" in result.output


def test_resolve_unknown_policy_exits_2(tmp_path: Path) -> None:
    g = _mk_entry("agent:a", "x.py", _h("1"), [], 1)
    f1 = _mk_entry("agent:a", "x.py", _h("2"), [entry_hash(g)], 2)
    f2 = _mk_entry("agent:b", "x.py", _h("3"), [entry_hash(g)], 3)
    log = _write_log(tmp_path, [g, f1, f2])
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        [
            "resolve",
            "x.py",
            "--policy",
            "bogus",
            "--log",
            str(log),
            "--sdd-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
