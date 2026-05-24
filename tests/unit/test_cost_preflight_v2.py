"""Tests for calibrated p50/p90 cost preflight band (feat-cost-preflight-v2)."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bernstein.core.cost.preflight import (
    DEFAULT_HISTORY_LIMIT,
    CostBand,
    compute_band,
    format_band,
    load_history,
)
from bernstein.core.cost.rate_cards import MissingRateCardError, lookup_rate_per_1k

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_history(path: Path, records: list[dict[str, object]]) -> None:
    """Serialise a list of records as ``cost.jsonl`` newline-delimited JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


@pytest.fixture()
def metrics_dir(tmp_path: Path) -> Path:
    """Return a fresh ``.sdd/metrics`` directory for the test."""
    target = tmp_path / ".sdd" / "metrics"
    target.mkdir(parents=True)
    return target


# ---------------------------------------------------------------------------
# load_history
# ---------------------------------------------------------------------------


def test_load_history_missing_file_returns_empty(metrics_dir: Path) -> None:
    """When ``cost.jsonl`` is absent, load_history returns ``[]``."""
    samples = load_history(metrics_dir / "cost.jsonl", role="backend", adapter="claude")
    assert samples == []


def test_load_history_filters_by_role_and_adapter(metrics_dir: Path) -> None:
    """Records for other (role, adapter) pairs must be excluded."""
    history = metrics_dir / "cost.jsonl"
    _write_history(
        history,
        [
            {"role": "backend", "adapter": "claude", "cost_usd": 1.0},
            {"role": "backend", "adapter": "codex", "cost_usd": 99.0},  # excluded
            {"role": "qa", "adapter": "claude", "cost_usd": 99.0},  # excluded
            {"role": "backend", "adapter": "claude", "cost_usd": 2.0},
        ],
    )

    samples = load_history(history, role="backend", adapter="claude")
    assert samples == [1.0, 2.0]


def test_load_history_accepts_cli_and_model_aliases(metrics_dir: Path) -> None:
    """Adapter id can be carried as ``cli`` or ``model`` when ``adapter`` missing."""
    history = metrics_dir / "cost.jsonl"
    _write_history(
        history,
        [
            {"role": "backend", "cli": "claude", "cost_usd": 1.0},
            {"role": "backend", "model": "claude", "cost_usd": 2.0},
        ],
    )

    samples = load_history(history, role="backend", adapter="claude")
    assert samples == [1.0, 2.0]


def test_load_history_skips_invalid_records(metrics_dir: Path) -> None:
    """Corrupt lines and non-finite costs must be silently skipped."""
    history = metrics_dir / "cost.jsonl"
    history.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps({"role": "backend", "adapter": "claude", "cost_usd": -1.0}),
                json.dumps({"role": "backend", "adapter": "claude", "cost_usd": "NaN"}),
                json.dumps({"role": "backend", "adapter": "claude", "cost_usd": "Infinity"}),
                json.dumps({"role": "backend", "adapter": "claude", "cost_usd": "oops"}),
                json.dumps({"role": "backend", "adapter": "claude", "cost_usd": 1.5}),
                "",
                json.dumps(["array-not-dict"]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    samples = load_history(history, role="backend", adapter="claude")
    assert samples == [1.5]


def test_cost_preflight_uses_explicit_finite_checks() -> None:
    """Avoid self-comparison NaN checks flagged by static analysis."""
    source = Path("src/bernstein/core/cost/preflight.py").read_text(encoding="utf-8")
    assert "amount != amount" not in source
    assert "cost != cost" not in source


def test_load_history_keeps_only_last_n(metrics_dir: Path) -> None:
    """When more than ``limit`` samples match, only the tail is kept."""
    history = metrics_dir / "cost.jsonl"
    records: list[dict[str, object]] = [
        {"role": "backend", "adapter": "claude", "cost_usd": float(i)} for i in range(120)
    ]
    _write_history(history, records)

    samples = load_history(history, role="backend", adapter="claude", limit=50)
    assert len(samples) == 50
    # Newest records win - the last 50 should be 70..119.
    assert samples[0] == 70.0
    assert samples[-1] == 119.0


# ---------------------------------------------------------------------------
# compute_band - cold-start
# ---------------------------------------------------------------------------


def test_compute_band_cold_start_falls_back_to_heuristic(metrics_dir: Path) -> None:
    """No history -> ``cold_start=True`` and the band derives from rate card."""
    band = compute_band(
        role="backend",
        adapter="claude",
        model="sonnet",
        task_count=4,
        metrics_dir=metrics_dir,
    )

    assert band.cold_start is True
    assert band.samples == 0
    assert band.p50 > 0.0
    assert band.p90 >= band.p50
    # Sonnet at blended $0.009/1k * (4 tasks * 50k tokens) = $1.80
    assert band.p50 == pytest.approx(1.80, abs=0.01)
    # 4 * 150 * 0.009 = $5.40
    assert band.p90 == pytest.approx(5.40, abs=0.01)


def test_compute_band_cold_start_label_in_format(metrics_dir: Path) -> None:
    """``format_band`` appends the cold-start label only when applicable."""
    band = compute_band(
        role="backend",
        adapter="claude",
        model="sonnet",
        task_count=1,
        metrics_dir=metrics_dir,
    )
    assert "(cold estimate)" in format_band(band)


# ---------------------------------------------------------------------------
# compute_band - mature pair
# ---------------------------------------------------------------------------


def test_compute_band_mature_pair_uses_history(metrics_dir: Path) -> None:
    """With > 50 samples we sample the percentiles from history, not heuristics."""
    history = metrics_dir / "cost.jsonl"
    # 100 deterministic samples 0.01..1.00 -- p50 ~= 0.50, p90 ~= 0.90.
    records: list[dict[str, object]] = [
        {"role": "backend", "adapter": "claude", "cost_usd": (i + 1) / 100.0} for i in range(100)
    ]
    _write_history(history, records)

    band = compute_band(
        role="backend",
        adapter="claude",
        model="sonnet",
        task_count=99,  # heuristic would dominate; ensure history wins
        metrics_dir=metrics_dir,
    )

    assert band.cold_start is False
    assert band.samples == DEFAULT_HISTORY_LIMIT  # tail of 50
    # Tail-sampling keeps records 51..100 (cost 0.51..1.00); p50 ~= 0.75,
    # p90 ~= 0.95.  Round to two decimals as the API contract requires.
    assert band.p50 == pytest.approx(0.75, abs=0.02)
    assert band.p90 == pytest.approx(0.95, abs=0.02)
    assert band.p90 >= band.p50


def test_compute_band_p90_never_below_p50(metrics_dir: Path) -> None:
    """Even with two-sample history, p90 is clamped >= p50."""
    history = metrics_dir / "cost.jsonl"
    _write_history(
        history,
        [
            {"role": "backend", "adapter": "claude", "cost_usd": 5.0},
            {"role": "backend", "adapter": "claude", "cost_usd": 5.0},
        ],
    )

    band = compute_band(
        role="backend",
        adapter="claude",
        model="sonnet",
        task_count=1,
        metrics_dir=metrics_dir,
    )
    assert band.p50 == pytest.approx(5.0)
    assert band.p90 == pytest.approx(5.0)
    assert band.p90 >= band.p50


def test_compute_band_rounds_to_two_decimals(metrics_dir: Path) -> None:
    """``p50`` and ``p90`` are rounded to two decimal places."""
    history = metrics_dir / "cost.jsonl"
    _write_history(
        history,
        [{"role": "backend", "adapter": "claude", "cost_usd": 1.23456789} for _ in range(10)],
    )

    band = compute_band(
        role="backend",
        adapter="claude",
        model="sonnet",
        task_count=1,
        metrics_dir=metrics_dir,
    )
    assert band.p50 == 1.23
    assert band.p90 == 1.23


# ---------------------------------------------------------------------------
# compute_band - mixed pair
# ---------------------------------------------------------------------------


def test_compute_band_mixed_pair_ignores_other_pairs(metrics_dir: Path) -> None:
    """A file that mixes pairs only consumes records matching the query."""
    history = metrics_dir / "cost.jsonl"
    records: list[dict[str, object]] = [{"role": "qa", "adapter": "codex", "cost_usd": 99.0} for _ in range(60)]
    # Only three matching records - small history, but still > 0 samples.
    records.extend(
        [
            {"role": "backend", "adapter": "claude", "cost_usd": 0.10},
            {"role": "backend", "adapter": "claude", "cost_usd": 0.20},
            {"role": "backend", "adapter": "claude", "cost_usd": 0.30},
        ]
    )
    _write_history(history, records)

    band = compute_band(
        role="backend",
        adapter="claude",
        model="sonnet",
        task_count=10,
        metrics_dir=metrics_dir,
    )

    assert band.cold_start is False
    assert band.samples == 3
    # 99.0 noise must not bleed in.
    assert band.p90 < 1.0
    assert band.p50 == pytest.approx(0.20, abs=0.05)


# ---------------------------------------------------------------------------
# Rate card
# ---------------------------------------------------------------------------


def test_lookup_rate_per_1k_known_models() -> None:
    """Known model identifiers resolve to a positive blended cost."""
    assert lookup_rate_per_1k("sonnet") > 0.0
    assert lookup_rate_per_1k("opus") > lookup_rate_per_1k("sonnet")


def test_lookup_rate_per_1k_strict_raises_for_unknown() -> None:
    """``strict=True`` raises :class:`MissingRateCardError` on unknown models."""
    with pytest.raises(MissingRateCardError):
        lookup_rate_per_1k("not-a-real-model-2099", strict=True)


def test_lookup_rate_per_1k_non_strict_falls_back() -> None:
    """``strict=False`` (default) returns a safe positive fallback."""
    rate = lookup_rate_per_1k("not-a-real-model-2099")
    assert rate > 0.0


def test_compute_band_unknown_model_uses_fallback_rate(metrics_dir: Path) -> None:
    """An unknown model still produces a finite, non-negative band."""
    band = compute_band(
        role="backend",
        adapter="claude",
        model="some-future-frontier-model",
        task_count=1,
        metrics_dir=metrics_dir,
    )
    assert band.cold_start is True
    assert band.p50 >= 0.0
    assert band.p90 >= band.p50


# ---------------------------------------------------------------------------
# CostBand / format_band shape
# ---------------------------------------------------------------------------


def test_cost_band_is_frozen() -> None:
    """``CostBand`` is a frozen dataclass."""
    band = CostBand(
        p50=1.0,
        p90=2.0,
        samples=5,
        cold_start=False,
        role="backend",
        adapter="claude",
        model="sonnet",
    )
    with pytest.raises(AttributeError):
        band.p50 = 9.0  # type: ignore[misc]


def test_format_band_warm_has_no_cold_label() -> None:
    """Warm band format omits the ``(cold estimate)`` suffix."""
    band = CostBand(
        p50=1.0,
        p90=2.0,
        samples=5,
        cold_start=False,
        role="backend",
        adapter="claude",
        model="sonnet",
    )
    text = format_band(band)
    assert "(cold estimate)" not in text
    assert "p50 $1.00" in text
    assert "p90 $2.00" in text
