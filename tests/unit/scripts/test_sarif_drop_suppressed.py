"""Unit tests for ``scripts/sarif_drop_suppressed.py``.

Covers the four documented cases from the spec:

- empty ``suppressions`` array on a result (keep)
- missing ``suppressions`` key (keep)
- non-empty ``suppressions`` array (drop)
- multi-run SARIF files (filter every run independently)

Also exercises the CLI surface (stdin->stdout, argv path -> stdout,
argv in + argv out) so the workflow invocations stay covered.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "sarif_drop_suppressed.py"


@pytest.fixture
def sarif_module() -> Generator[ModuleType, None, None]:
    """Load scripts/sarif_drop_suppressed.py as a module."""
    spec = importlib.util.spec_from_file_location("sarif_drop_suppressed_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _result(rule_id: str, suppressions: Any | None = ...) -> dict[str, Any]:
    """Build a minimal SARIF result; pass ``suppressions=...`` to omit the key."""
    out: dict[str, Any] = {
        "ruleId": rule_id,
        "level": "warning",
        "message": {"text": f"finding {rule_id}"},
    }
    if suppressions is not ...:
        out["suppressions"] = suppressions
    return out


def _sarif(*results_per_run: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": "2.1.0",
        "$schema": "https://example.com/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": f"tool-{idx}"}},
                "results": results.copy(),
            }
            for idx, results in enumerate(results_per_run)
        ],
    }


def _ids(sarif: dict[str, Any], run: int = 0) -> list[str]:
    return [r["ruleId"] for r in sarif["runs"][run]["results"]]


# ---------------------------------------------------------------------------
# filter_sarif unit cases
# ---------------------------------------------------------------------------


def test_keeps_result_with_empty_suppressions_array(sarif_module) -> None:
    sarif = _sarif([_result("R1", suppressions=[])])
    out = sarif_module.filter_sarif(sarif)
    assert _ids(out) == ["R1"]


def test_keeps_result_with_missing_suppressions_key(sarif_module) -> None:
    sarif = _sarif([_result("R2")])
    out = sarif_module.filter_sarif(sarif)
    assert _ids(out) == ["R2"]


def test_keeps_result_with_null_suppressions(sarif_module) -> None:
    sarif = _sarif([_result("R3", suppressions=None)])
    out = sarif_module.filter_sarif(sarif)
    assert _ids(out) == ["R3"]


def test_drops_result_with_nonempty_suppressions(sarif_module) -> None:
    suppression = {"kind": "inSource", "justification": "# nosemgrep: R4"}
    sarif = _sarif(
        [
            _result("R4", suppressions=[suppression]),
            _result("R5"),
        ]
    )
    out = sarif_module.filter_sarif(sarif)
    assert _ids(out) == ["R5"]


def test_multi_run_filters_each_run_independently(sarif_module) -> None:
    suppression = {"kind": "inSource"}
    sarif = _sarif(
        [
            _result("A1", suppressions=[suppression]),
            _result("A2"),
        ],
        [
            _result("B1", suppressions=[]),
            _result("B2", suppressions=[suppression, suppression]),
        ],
    )
    out = sarif_module.filter_sarif(sarif)
    assert _ids(out, 0) == ["A2"]
    assert _ids(out, 1) == ["B1"]


def test_preserves_tool_and_top_level_fields(sarif_module) -> None:
    suppression = {"kind": "inSource"}
    sarif = _sarif([_result("X1", suppressions=[suppression]), _result("X2")])
    sarif["runs"][0]["invocations"] = [{"executionSuccessful": True}]
    sarif["runs"][0]["properties"] = {"category": "semgrep-ce"}
    out = sarif_module.filter_sarif(sarif)
    assert out["version"] == "2.1.0"
    assert out["$schema"].endswith("sarif-2.1.0.json")
    assert out["runs"][0]["tool"] == {"driver": {"name": "tool-0"}}
    assert out["runs"][0]["invocations"] == [{"executionSuccessful": True}]
    assert out["runs"][0]["properties"] == {"category": "semgrep-ce"}
    assert _ids(out) == ["X2"]


def test_run_without_results_key_is_left_alone(sarif_module) -> None:
    sarif = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "t"}}}]}
    out = sarif_module.filter_sarif(sarif)
    assert out == sarif


def test_no_runs_key_passes_through(sarif_module) -> None:
    sarif = {"version": "2.1.0"}
    out = sarif_module.filter_sarif(sarif)
    assert out == sarif


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _run_cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_stdin_to_stdout(tmp_path: Path) -> None:
    sarif = _sarif([_result("Y1", suppressions=[{"kind": "inSource"}]), _result("Y2")])
    proc = _run_cli(stdin=json.dumps(sarif))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert [r["ruleId"] for r in out["runs"][0]["results"]] == ["Y2"]


def test_cli_argv_input_to_stdout(tmp_path: Path) -> None:
    sarif = _sarif([_result("Z1", suppressions=[{"kind": "inSource"}]), _result("Z2")])
    in_path = tmp_path / "in.sarif"
    in_path.write_text(json.dumps(sarif), encoding="utf-8")
    proc = _run_cli(str(in_path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert [r["ruleId"] for r in out["runs"][0]["results"]] == ["Z2"]


def test_cli_argv_input_and_output(tmp_path: Path) -> None:
    sarif = _sarif([_result("Q1", suppressions=[{"kind": "inSource"}]), _result("Q2")])
    in_path = tmp_path / "in.sarif"
    out_path = tmp_path / "out.sarif"
    in_path.write_text(json.dumps(sarif), encoding="utf-8")
    proc = _run_cli(str(in_path), str(out_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    out = json.loads(out_path.read_text(encoding="utf-8"))
    assert [r["ruleId"] for r in out["runs"][0]["results"]] == ["Q2"]


def test_cli_rejects_too_many_args(tmp_path: Path) -> None:
    proc = _run_cli("a", "b", "c")
    assert proc.returncode == 2
    assert "usage" in proc.stderr.lower()
