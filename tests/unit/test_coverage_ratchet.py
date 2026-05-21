"""Unit tests for the total-coverage monotonic ratchet.

Covers ``scripts/coverage_ratchet.py``:

- parsing total line coverage from a Cobertura ``coverage.xml``;
- the compare/bump decision (drop -> fail, rise -> pass + high-water bump,
  flat -> pass + no write);
- atomic baseline read/write (no partial file on crash);
- the weekly diff-coverage floor increment with its cap;
- graceful handling of a malformed / missing ``coverage.xml``.

The script is import-only at module level (no side effects) so these
tests can drive its functions directly without spawning a subprocess.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

# ``scripts/`` is not an installed package, so load the module by path.
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "coverage_ratchet.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coverage_ratchet", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register so dataclasses / typing resolve a stable module identity.
    sys.modules["coverage_ratchet"] = module
    spec.loader.exec_module(module)
    return module


ratchet = _load_module()


# --------------------------------------------------------------------------- #
# coverage.xml parsing
# --------------------------------------------------------------------------- #


def _write_coverage_xml(path: Path, line_rate: str) -> None:
    """Write a minimal Cobertura coverage.xml with the given root line-rate."""
    path.write_text(
        f'<?xml version="1.0" ?>\n'
        f'<coverage line-rate="{line_rate}" branch-rate="0.1" version="7.0">\n'
        f"  <packages></packages>\n"
        f"</coverage>\n",
        encoding="utf-8",
    )


def test_parse_total_coverage_reads_root_line_rate(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    _write_coverage_xml(xml, "0.1753")

    pct = ratchet.parse_total_coverage(xml)

    assert pct == pytest.approx(17.53, abs=0.001)


def test_parse_total_coverage_full_coverage(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    _write_coverage_xml(xml, "1.0")

    assert ratchet.parse_total_coverage(xml) == pytest.approx(100.0, abs=0.001)


def test_parse_total_coverage_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ratchet.CoverageParseError):
        ratchet.parse_total_coverage(tmp_path / "does-not-exist.xml")


def test_parse_total_coverage_malformed_xml_raises(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text("<coverage line-rate=", encoding="utf-8")  # truncated, invalid

    with pytest.raises(ratchet.CoverageParseError):
        ratchet.parse_total_coverage(xml)


def test_parse_total_coverage_missing_attribute_raises(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    xml.write_text('<?xml version="1.0" ?>\n<coverage version="7.0"></coverage>\n', encoding="utf-8")

    with pytest.raises(ratchet.CoverageParseError):
        ratchet.parse_total_coverage(xml)


def test_parse_total_coverage_non_numeric_attribute_raises(tmp_path: Path) -> None:
    xml = tmp_path / "coverage.xml"
    _write_coverage_xml(xml, "not-a-number")

    with pytest.raises(ratchet.CoverageParseError):
        ratchet.parse_total_coverage(xml)


def test_parse_total_coverage_empty_tree_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A parse that yields no root element is reported, not an AttributeError."""
    xml = tmp_path / "coverage.xml"
    _write_coverage_xml(xml, "0.5")

    class _RootlessTree:
        def getroot(self) -> None:
            return None

    monkeypatch.setattr(ratchet.ET, "parse", lambda _path: _RootlessTree())

    with pytest.raises(ratchet.CoverageParseError):
        ratchet.parse_total_coverage(xml)


# --------------------------------------------------------------------------- #
# baseline read / write
# --------------------------------------------------------------------------- #


def test_read_baseline_round_trips(tmp_path: Path) -> None:
    baseline_path = tmp_path / ".coverage-baseline.json"
    written = ratchet.Baseline(total_coverage_percent=17.5, diff_coverage_floor_percent=80)
    ratchet.write_baseline(baseline_path, written)

    loaded = ratchet.read_baseline(baseline_path)

    assert loaded.total_coverage_percent == pytest.approx(17.5)
    assert loaded.diff_coverage_floor_percent == 80


def test_read_baseline_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ratchet.read_baseline(tmp_path / "nope.json")


def test_write_baseline_is_atomic_no_partial_temp_left(tmp_path: Path) -> None:
    """A successful write leaves exactly the target file, no temp turds."""
    baseline_path = tmp_path / ".coverage-baseline.json"
    ratchet.write_baseline(
        baseline_path,
        ratchet.Baseline(total_coverage_percent=20.0, diff_coverage_floor_percent=85),
    )

    siblings = list(tmp_path.iterdir())
    assert siblings == [baseline_path], f"unexpected leftover files: {siblings}"
    # File is valid JSON with the documented keys.
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert set(data) >= {"total_coverage_percent", "diff_coverage_floor_percent"}


def test_write_baseline_does_not_corrupt_existing_on_serialise_failure(tmp_path: Path) -> None:
    """If serialisation blows up mid-write, the prior baseline survives."""
    baseline_path = tmp_path / ".coverage-baseline.json"
    good = ratchet.Baseline(total_coverage_percent=17.5, diff_coverage_floor_percent=80)
    ratchet.write_baseline(baseline_path, good)
    original_bytes = baseline_path.read_bytes()

    class _Unserialisable:
        pass

    bad = ratchet.Baseline(
        total_coverage_percent=_Unserialisable(),  # type: ignore[arg-type]
        diff_coverage_floor_percent=80,
    )
    with pytest.raises(TypeError):
        ratchet.write_baseline(baseline_path, bad)

    # Atomic replace means the original content is untouched.
    assert baseline_path.read_bytes() == original_bytes


# --------------------------------------------------------------------------- #
# compare / bump decision
# --------------------------------------------------------------------------- #


def test_decide_drop_fails_and_does_not_bump() -> None:
    decision = ratchet.decide(baseline_pct=17.5, measured_pct=16.0, tolerance=0.05)

    assert decision.dropped is True
    assert decision.should_bump is False
    assert decision.exit_code != 0


def test_decide_rise_passes_and_bumps() -> None:
    decision = ratchet.decide(baseline_pct=17.5, measured_pct=19.2, tolerance=0.05)

    assert decision.dropped is False
    assert decision.should_bump is True
    assert decision.new_total_pct == pytest.approx(19.2)
    assert decision.exit_code == 0


def test_decide_flat_within_tolerance_passes_without_bump() -> None:
    decision = ratchet.decide(baseline_pct=17.50, measured_pct=17.52, tolerance=0.05)

    assert decision.dropped is False
    assert decision.should_bump is False
    assert decision.exit_code == 0


def test_decide_tiny_drop_within_tolerance_does_not_fail() -> None:
    """Sub-tolerance noise (float jitter between runs) must not trip the gate."""
    decision = ratchet.decide(baseline_pct=17.50, measured_pct=17.48, tolerance=0.05)

    assert decision.dropped is False
    assert decision.exit_code == 0


def test_decide_drop_beyond_tolerance_fails() -> None:
    decision = ratchet.decide(baseline_pct=17.50, measured_pct=17.40, tolerance=0.05)

    assert decision.dropped is True
    assert decision.exit_code != 0


# --------------------------------------------------------------------------- #
# weekly diff-coverage floor increment + cap
# --------------------------------------------------------------------------- #


def test_weekly_bump_increments_by_step() -> None:
    assert ratchet.next_floor(current=80, step=1, cap=90) == 81


def test_weekly_bump_caps_at_ceiling() -> None:
    assert ratchet.next_floor(current=90, step=1, cap=90) == 90


def test_weekly_bump_does_not_overshoot_cap() -> None:
    assert ratchet.next_floor(current=89, step=5, cap=90) == 90


def test_weekly_bump_already_above_cap_clamps_down_to_cap() -> None:
    # Defensive: a manually-edited floor above the cap is clamped, never raised.
    assert ratchet.next_floor(current=95, step=1, cap=90) == 90


def test_weekly_bump_rejects_non_positive_step() -> None:
    with pytest.raises(ValueError):
        ratchet.next_floor(current=80, step=0, cap=90)
