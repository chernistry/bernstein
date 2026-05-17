"""CLI tests for ``bernstein lineage v2 ...``.

Covers ``show``, ``verify``, and ``export`` (jsonl + sigstore) plus
empty-store paths and exit-code semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from bernstein.cli.commands.lineage_cmd import lineage_cmd
from bernstein.core.lineage.v2_store import (
    LINEAGE_V2_ENTRY_VERSION,
    ChildBody,
    LineageV2Store,
    ParentRef,
)


def _pref(task: str = "T", run: str = "r", summary: str = "s") -> ParentRef:
    return ParentRef(
        v=LINEAGE_V2_ENTRY_VERSION,
        task_id=task,
        child_run_id=run,
        parent_call_id="call",
        summary=summary,
        child_sha="sha256:" + "0" * 64,
        ts_ns=1,
        prev_hmac="",
        hmac="",
    )


def _cbody(task: str = "T", run: str = "r", seq: int = 0) -> ChildBody:
    return ChildBody(
        v=LINEAGE_V2_ENTRY_VERSION,
        task_id=task,
        child_run_id=run,
        seq=seq,
        kind="subagent.started",
        payload={"x": 1},
        ts_ns=1,
    )


def test_cli_v2_show_empty_store(tmp_path: Path) -> None:
    runner = CliRunner()
    root = tmp_path / "v2"
    root.mkdir()
    result = runner.invoke(lineage_cmd, ["v2", "show", "missing", "--root", str(root)])
    assert result.exit_code == 0
    assert "No v2 records" in result.output


def test_cli_v2_show_renders_table(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(task="T", run="r1", summary="step-1"), _cbody(task="T", run="r1"))
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "show", "T", "--root", str(root)])
    assert result.exit_code == 0
    assert "r1" in result.output
    assert "step-1" in result.output


def test_cli_v2_show_json(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(task="T", run="r1"), _cbody(task="T", run="r1"))
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "show", "T", "--root", str(root), "--output-json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["parent"]["task_id"] == "T"


def test_cli_v2_verify_passes_empty(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    root.mkdir()
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "verify", "--root", str(root)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_cli_v2_verify_passes_populated(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(), _cbody())
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "verify", "--root", str(root)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_cli_v2_verify_fails_on_tamper(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(), _cbody())
    # Tamper.
    log = root / "parent.jsonl"
    obj = json.loads(log.read_text().strip())
    obj["summary"] = "TAMPERED"
    log.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n")
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "verify", "--root", str(root)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_v2_verify_json_output(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(), _cbody())
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "verify", "--root", str(root), "--output-json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["parent_count"] == 1


def test_cli_v2_export_jsonl_stdout(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(task="T"), _cbody(task="T"))
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "export", "T", "--format", "jsonl", "--root", str(root)])
    assert result.exit_code == 0
    lines = [l for l in result.output.strip().split("\n") if l]
    assert len(lines) == 2
    head = json.loads(lines[0])
    assert head["_kind"] == "parent"


def test_cli_v2_export_jsonl_to_file(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(task="T"), _cbody(task="T"))
    out = tmp_path / "out.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        ["v2", "export", "T", "--format", "jsonl", "--root", str(root), "--output", str(out)],
    )
    assert result.exit_code == 0
    assert out.exists()
    assert out.read_text().count("\n") >= 2


def test_cli_v2_export_sigstore(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(task="T"), _cbody(task="T"))
    runner = CliRunner()
    result = runner.invoke(lineage_cmd, ["v2", "export", "T", "--format", "sigstore", "--root", str(root)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["predicateType"] == "https://slsa.dev/provenance/v0.3"


def test_cli_v2_export_sigstore_to_file(tmp_path: Path) -> None:
    root = tmp_path / "v2"
    store = LineageV2Store(root)
    store.append(_pref(task="T"), _cbody(task="T"))
    out = tmp_path / "att.json"
    runner = CliRunner()
    result = runner.invoke(
        lineage_cmd,
        ["v2", "export", "T", "--format", "sigstore", "--root", str(root), "--output", str(out)],
    )
    assert result.exit_code == 0
    payload = json.loads(out.read_text())
    assert isinstance(payload, list)
