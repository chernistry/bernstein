"""Unit tests for ``scripts/auto_heal_recurrence.py``."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_SPEC = importlib.util.spec_from_file_location("auto_heal_recurrence", _SCRIPTS / "auto_heal_recurrence.py")
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

count_recurrences = _MOD.count_recurrences
main = _MOD.main


def test_count_recurrences_empty_records() -> None:
    assert count_recurrences([], ["safe"]) == 0


def test_count_recurrences_empty_needles() -> None:
    records = [{"body": "**safe**", "mergedAt": None}]
    assert count_recurrences(records, []) == 0


def test_count_recurrences_skips_merged() -> None:
    records = [
        {"body": "**safe**", "mergedAt": "2026-05-17T00:00:00Z"},
        {"body": "**safe**", "mergedAt": None},
    ]
    assert count_recurrences(records, ["safe"]) == 1


def test_count_recurrences_matches_bold_marker_only() -> None:
    records = [
        {"body": "safe class but not bold", "mergedAt": None},
        {"body": "Has **safe** bold marker", "mergedAt": None},
    ]
    assert count_recurrences(records, ["safe"]) == 1


def test_count_recurrences_any_needle_matches() -> None:
    records = [
        {"body": "**heuristic** mentions", "mergedAt": None},
    ]
    assert count_recurrences(records, ["safe", "heuristic"]) == 1


def test_count_recurrences_multiple_records() -> None:
    records = [
        {"body": "**safe**", "mergedAt": None},
        {"body": "**safe**", "mergedAt": None},
        {"body": "**safe**", "mergedAt": "2026-05-17T00:00:00Z"},
    ]
    assert count_recurrences(records, ["safe"]) == 2


def test_main_missing_path_returns_zero(tmp_path: Path, capsys: object) -> None:
    rc = main(["prog", str(tmp_path / "missing.json")])
    assert rc == 0
    out = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert out == "0"


def test_main_empty_file_returns_zero(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "data.json"
    path.write_text("")
    rc = main(["prog", str(path)])
    assert rc == 0
    out = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert out == "0"


def test_main_invalid_json_returns_zero(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "data.json"
    path.write_text("not json")
    rc = main(["prog", str(path)])
    assert rc == 0
    out = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert out == "0"


def test_main_reads_needles_from_env(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "data.json"
    payload = [
        {"body": "**safe** stuff", "mergedAt": None},
        {"body": "**heuristic** stuff", "mergedAt": None},
    ]
    path.write_text(json.dumps(payload))
    with mock.patch.dict(__import__("os").environ, {"NEEDLES": "safe heuristic"}, clear=False):
        rc = main(["prog", str(path)])
    assert rc == 0
    out = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert out == "2"


def test_main_missing_argv_returns_one() -> None:
    rc = main(["prog"])
    assert rc == 1


def test_main_no_needles_env_returns_zero(tmp_path: Path, capsys: object) -> None:
    path = tmp_path / "data.json"
    path.write_text(json.dumps([{"body": "**safe**", "mergedAt": None}]))
    # Drop NEEDLES from env.
    env = {k: v for k, v in __import__("os").environ.items() if k != "NEEDLES"}
    with mock.patch.dict(__import__("os").environ, env, clear=True):
        rc = main(["prog", str(path)])
    assert rc == 0
    out = capsys.readouterr().out.strip()  # type: ignore[attr-defined]
    assert out == "0"
