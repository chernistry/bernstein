"""Per-quota-envelope cost rollup (issue #1405).

Aggregates :class:`bernstein.core.cost.cost_tracker.TokenUsage` records
into per-envelope spend / cap / burn reports. The rollup is a pure
function over a list of records so it can be driven from any source
(in-memory tracker, JSONL ledger, persisted run snapshot).

Caps are read from operator-supplied :class:`EnvelopeConfig` mappings;
when an envelope is observed without a configured cap the report shows
``cap_usd = 0.0`` (unlimited) and ``pct_used = 0.0``.

Forecast-to-cap is a linear extrapolation: ``cap_usd / burn_rate``. Burn
rate is ``spent_usd / window_seconds`` using the first-to-last record
timestamp window. When fewer than two records exist or the window is
zero we return ``None`` for the forecast so dashboards can render
``"--"`` instead of a misleading projection.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from bernstein.core.cost.cost_tracker import (
    DEFAULT_ENVELOPE_THRESHOLD,
    DEFAULT_QUOTA_ENVELOPE,
    EnvelopeConfig,
    EnvelopeReport,
    TokenUsage,
)


@dataclass(frozen=True)
class EnvelopeRollupRow:
    """One row of the per-envelope rollup.

    Attributes:
        name: Envelope identifier.
        total_spend: Cumulative cost attributed to this envelope.
        cap: Soft cap from :class:`EnvelopeConfig.budget_usd` (``0`` =
            unlimited).
        hard_cap: Hard cap; ``0`` = unlimited.
        pct_used: ``total_spend / cap`` when ``cap > 0`` else ``0.0``.
        threshold_pct: Configured threshold-hook fraction.
        threshold_reached: ``True`` when ``pct_used >= threshold_pct``.
        hard_breached: ``True`` when ``total_spend >= hard_cap`` (and the
            hard cap is set).
        forecast_to_cap_seconds: Seconds until the soft cap is exhausted
            at the current burn rate, or ``None`` when the projection is
            undefined.
        calls: Number of LLM calls attributed to this envelope.
        models: Sorted tuple of distinct model names observed.
        first_ts: Earliest record timestamp; ``0.0`` when empty.
        last_ts: Latest record timestamp; ``0.0`` when empty.
        burn_rate_usd_per_sec: Average spend rate over the observed
            window (``0.0`` when the window collapses to a point).
    """

    name: str
    total_spend: float
    cap: float
    hard_cap: float
    pct_used: float
    threshold_pct: float
    threshold_reached: bool
    hard_breached: bool
    forecast_to_cap_seconds: float | None
    calls: int
    models: tuple[str, ...]
    first_ts: float
    last_ts: float
    burn_rate_usd_per_sec: float

    def to_envelope_report(self) -> EnvelopeReport:
        """Return the equivalent live :class:`EnvelopeReport`.

        Useful for dashboard tiles that already render
        :class:`EnvelopeReport` from the live tracker — the rollup row
        carries the same fields when driven from a historical ledger.
        """
        remaining = max(self.cap - self.total_spend, 0.0) if self.cap > 0 else float("inf")
        hard_remaining = max(self.hard_cap - self.total_spend, 0.0) if self.hard_cap > 0 else float("inf")
        return EnvelopeReport(
            name=self.name,
            spent_usd=self.total_spend,
            cap_usd=self.cap,
            hard_cap_usd=self.hard_cap,
            pct_used=self.pct_used,
            threshold_pct=self.threshold_pct,
            calls=self.calls,
            remaining_usd=remaining,
            hard_remaining_usd=hard_remaining,
            threshold_reached=self.threshold_reached,
            hard_breached=self.hard_breached,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "name": self.name,
            "total_spend": round(self.total_spend, 6),
            "cap": self.cap,
            "hard_cap": self.hard_cap,
            "pct_used": round(self.pct_used, 4),
            "threshold_pct": self.threshold_pct,
            "threshold_reached": self.threshold_reached,
            "hard_breached": self.hard_breached,
            "forecast_to_cap_seconds": self.forecast_to_cap_seconds,
            "calls": self.calls,
            "models": list(self.models),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "burn_rate_usd_per_sec": self.burn_rate_usd_per_sec,
        }


@dataclass
class _EnvelopeAccum:
    """Running accumulator used during ``rollup()``."""

    spend: float = 0.0
    calls: int = 0
    models: set[str] = field(default_factory=set[str])
    first_ts: float = 0.0
    last_ts: float = 0.0


def rollup(
    records: list[TokenUsage],
    envelopes: dict[str, EnvelopeConfig] | None = None,
    *,
    now: float | None = None,
) -> dict[str, EnvelopeRollupRow]:
    """Roll a list of :class:`TokenUsage` rows up into per-envelope reports.

    Records without an explicit ``quota_envelope`` fall under
    :data:`DEFAULT_QUOTA_ENVELOPE`. Configured envelopes that received
    zero spend still appear in the output so dashboards can show "$0 of
    $X" tiles without merging two sources.

    Args:
        records: Token-usage rows to aggregate.
        envelopes: Operator-supplied envelope configuration. ``None`` is
            equivalent to an empty mapping.
        now: Wall-clock timestamp used for the burn-rate window. Defaults
            to :func:`time.time` and is exposed for deterministic tests.

    Returns:
        Mapping from envelope name to :class:`EnvelopeRollupRow`. Keys
        are sorted for stable iteration order.
    """
    env_cfg = dict(envelopes or {})
    accum: dict[str, _EnvelopeAccum] = {name: _EnvelopeAccum() for name in env_cfg}

    for rec in records:
        name = rec.quota_envelope or DEFAULT_QUOTA_ENVELOPE
        bucket = accum.get(name)
        if bucket is None:
            bucket = _EnvelopeAccum()
            accum[name] = bucket
        bucket.spend += rec.cost_usd
        bucket.calls += 1
        if rec.model:
            bucket.models.add(rec.model)
        ts = rec.timestamp
        if ts > 0:
            if bucket.first_ts == 0.0 or ts < bucket.first_ts:
                bucket.first_ts = ts
            if ts > bucket.last_ts:
                bucket.last_ts = ts

    snap_now = now if now is not None else time.time()

    out: dict[str, EnvelopeRollupRow] = {}
    for name in sorted(accum):
        bucket = accum[name]
        cfg = env_cfg.get(name)
        cap = cfg.budget_usd if cfg is not None else 0.0
        hard_cap = cfg.hard_budget_usd if cfg is not None else 0.0
        threshold = cfg.threshold_pct if cfg is not None else DEFAULT_ENVELOPE_THRESHOLD
        pct = (bucket.spend / cap) if cap > 0 else 0.0
        threshold_reached = cap > 0 and pct >= threshold
        hard_breached = hard_cap > 0 and bucket.spend >= hard_cap

        # Burn-rate window: prefer first-to-last record span; fall back
        # to first-to-now when only one record exists so a long-running
        # idle envelope still produces a finite forecast.
        if bucket.first_ts > 0 and bucket.last_ts > bucket.first_ts:
            window_s = bucket.last_ts - bucket.first_ts
        elif bucket.first_ts > 0 and snap_now > bucket.first_ts:
            window_s = snap_now - bucket.first_ts
        else:
            window_s = 0.0

        burn_rate = (bucket.spend / window_s) if window_s > 0 else 0.0
        if cap > 0 and burn_rate > 0:
            remaining = max(cap - bucket.spend, 0.0)
            forecast: float | None = remaining / burn_rate
        else:
            forecast = None

        out[name] = EnvelopeRollupRow(
            name=name,
            total_spend=bucket.spend,
            cap=cap,
            hard_cap=hard_cap,
            pct_used=pct,
            threshold_pct=threshold,
            threshold_reached=threshold_reached,
            hard_breached=hard_breached,
            forecast_to_cap_seconds=forecast,
            calls=bucket.calls,
            models=tuple(sorted(bucket.models)),
            first_ts=bucket.first_ts,
            last_ts=bucket.last_ts,
            burn_rate_usd_per_sec=burn_rate,
        )

    return out


def aggregate_totals(rows: dict[str, EnvelopeRollupRow]) -> dict[str, float]:
    """Sum spend / cap / hard_cap across every envelope.

    Convenience helper for the CLI total row. Returned keys:
    ``total_spend``, ``total_cap``, ``total_hard_cap``, ``total_calls``.
    """
    total_spend = 0.0
    total_cap = 0.0
    total_hard = 0.0
    total_calls = 0
    for row in rows.values():
        total_spend += row.total_spend
        total_cap += row.cap
        total_hard += row.hard_cap
        total_calls += row.calls
    return {
        "total_spend": total_spend,
        "total_cap": total_cap,
        "total_hard_cap": total_hard,
        "total_calls": float(total_calls),
    }


__all__ = [
    "EnvelopeRollupRow",
    "aggregate_totals",
    "rollup",
]
