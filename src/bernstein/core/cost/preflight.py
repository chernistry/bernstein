"""Calibrated p50/p90 cost preflight band.

Replaces the single-point heuristic with a percentile band computed from
the last ``N`` runs of the same ``(role, adapter)`` pair persisted to
``.sdd/metrics/cost.jsonl``. Cold-start (no history) falls back to the
legacy heuristic and the band is labelled ``(cold estimate)``.

The estimator is intentionally additive and side-effect-free:

* Read-only over the metrics store. No schema migration.
* No NumPy dependency: uses :func:`statistics.quantiles`.
* No live pricing fetch. Rate cards are sourced from
  :mod:`bernstein.core.cost.rate_cards`.

Schema (lenient parser):

Each line of ``cost.jsonl`` is a JSON object. The estimator looks for the
following fields and tolerates missing/extra keys:

* ``role``        -- string, must match (case-insensitive) the queried role
* ``adapter``     -- string adapter id (``"claude"``, ``"codex"``...). The
  parser also accepts ``cli`` or ``model`` aliases when ``adapter`` is
  absent.
* ``cost_usd``    -- number, finite and ``>= 0``. Records failing this
  check are skipped, never coerced to NaN/negative.

Any other shape is silently skipped; a corrupt history file must never
abort the preflight.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from bernstein.core.cost.rate_cards import lookup_rate_per_1k

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "CostBand",
    "compute_band",
    "format_band",
    "load_history",
]

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT: Final[int] = 50
"""Last ``N`` records of the same (role, adapter) pair to consider."""

_COLD_START_LABEL: Final[str] = "(cold estimate)"

# Heuristic token band for the cold-start fallback. Matches the legacy
# ``estimate_run_cost`` envelope (50k-150k tokens per task, expressed in
# units of "1k tokens" so the multiplication against the per-1k rate is
# direct).
_HEURISTIC_TOKENS_PER_TASK_LOW: Final[float] = 50.0
_HEURISTIC_TOKENS_PER_TASK_HIGH: Final[float] = 150.0


@dataclass(frozen=True)
class CostBand:
    """Calibrated p50/p90 cost preflight band.

    Attributes:
        p50: Median estimated USD spend for the run.
        p90: 90th-percentile estimated USD spend for the run.
        samples: Number of historical samples backing the band. ``0`` for
            cold-start.
        cold_start: True when the band was produced by the heuristic
            fallback rather than from history.
        role: Queried role (e.g. ``"backend"``).
        adapter: Queried adapter identifier (e.g. ``"claude"``).
        model: Model used to drive the cold-start heuristic. Empty when
            unknown / not provided.
    """

    p50: float
    p90: float
    samples: int
    cold_start: bool
    role: str
    adapter: str
    model: str

    @property
    def label(self) -> str:
        """Suffix appended to the formatted band when cold-started."""
        return _COLD_START_LABEL if self.cold_start else ""


def _sanitize(value: float) -> float:
    """Clamp a USD figure to a safe display value.

    Negative values, NaNs and infinities are mapped to ``0.0``; everything
    else is rounded to two decimals.
    """
    try:
        amount = value
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(amount) or amount < 0.0:
        return 0.0
    return round(amount, 2)


def _extract_adapter(record: dict[str, object]) -> str | None:
    """Pull the adapter id from a record, accepting aliases."""
    for key in ("adapter", "cli", "model"):
        raw = record.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
    return None


def _matches(
    record: dict[str, object],
    role: str,
    adapter: str,
) -> bool:
    """Return True when ``record`` belongs to the queried (role, adapter)."""
    record_role = record.get("role")
    if not isinstance(record_role, str) or record_role.strip().lower() != role:
        return False
    record_adapter = _extract_adapter(record)
    return record_adapter == adapter


def load_history(
    metrics_path: Path,
    *,
    role: str,
    adapter: str,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[float]:
    """Return the last ``limit`` USD costs for ``(role, adapter)``.

    Args:
        metrics_path: Path to ``.sdd/metrics/cost.jsonl``. A missing file or
            unreadable file yields an empty list (cold-start).
        role: Role to filter on, case-insensitive (e.g. ``"backend"``).
        adapter: Adapter id to filter on, case-insensitive (e.g.
            ``"claude"``).
        limit: Maximum samples to retain (most recent records win).

    Returns:
        List of finite, non-negative USD figures, newest-last preserved
        in file order. Empty when no history is available.
    """
    if limit <= 0 or not metrics_path.exists() or not metrics_path.is_file():
        return []

    role_norm = role.strip().lower()
    adapter_norm = adapter.strip().lower()
    matched: list[float] = []

    try:
        text = metrics_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive
        logger.debug("preflight: cost history unreadable at %s: %s", metrics_path, exc)
        return []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        record: dict[str, object] = {
            str(k): v
            for k, v in parsed.items()  # type: ignore[reportUnknownVariableType]
        }
        if not _matches(record, role_norm, adapter_norm):
            continue
        cost_raw = record.get("cost_usd")
        try:
            cost = float(cost_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not math.isfinite(cost) or cost < 0.0:
            continue
        matched.append(cost)

    if len(matched) > limit:
        matched = matched[-limit:]
    return matched


def _percentile(samples: list[float], q: float) -> float:
    """Return the ``q``-th percentile of ``samples`` (``q`` in 0..1).

    Uses :func:`statistics.quantiles` with the inclusive method, which
    matches the canonical "linear interpolation between order statistics"
    definition used by NumPy ``percentile`` for the same percentile.

    For samples of length ``<= 1`` we degrade to the single value so we
    never raise on micro-history.
    """
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    # ``quantiles(n=100)`` returns the 99 cutpoints; index ``q*100 - 1``.
    idx = round(q * 100) - 1
    idx = max(0, min(98, idx))
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return cuts[idx]


def _heuristic_band(
    *,
    task_count: int,
    model: str,
) -> tuple[float, float]:
    """Return the legacy single-point heuristic as a (p50, p90)-shaped band.

    The legacy heuristic is a width-bounded range, not a true band; we
    map ``low`` -> p50 and ``high`` -> p90 so the new caller sees a band
    of the expected shape even on a fully cold workspace.
    """
    rate = lookup_rate_per_1k(model)
    tasks = max(1, task_count)
    p50 = tasks * _HEURISTIC_TOKENS_PER_TASK_LOW * rate
    p90 = tasks * _HEURISTIC_TOKENS_PER_TASK_HIGH * rate
    return (p50, p90)


def compute_band(
    *,
    role: str,
    adapter: str,
    model: str,
    task_count: int,
    metrics_dir: Path,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> CostBand:
    """Compute the calibrated p50/p90 preflight band.

    Args:
        role: Role driving the planned run (e.g. ``"backend"``).
        adapter: Adapter id (e.g. ``"claude"``, ``"codex"``).
        model: Model name used by the cold-start heuristic.
        task_count: Number of tasks planned. Multiplies the heuristic but
            does not affect historical samples (those already reflect
            per-run totals).
        metrics_dir: Path to ``.sdd/metrics``. ``cost.jsonl`` is the only
            file consulted.
        history_limit: Max samples to retain from history (newest wins).

    Returns:
        :class:`CostBand` with rounded, non-negative ``p50`` and ``p90``.
    """
    role_norm = role.strip().lower()
    adapter_norm = adapter.strip().lower()
    history_path = metrics_dir / "cost.jsonl"

    samples = load_history(
        history_path,
        role=role_norm,
        adapter=adapter_norm,
        limit=history_limit,
    )

    if samples:
        p50_raw = _percentile(samples, 0.5)
        p90_raw = _percentile(samples, 0.9)
        # Order invariant: p90 must never sit below p50, even with
        # heavily-skewed micro-samples.
        if p90_raw < p50_raw:
            p90_raw = p50_raw
        return CostBand(
            p50=_sanitize(p50_raw),
            p90=_sanitize(p90_raw),
            samples=len(samples),
            cold_start=False,
            role=role_norm,
            adapter=adapter_norm,
            model=model,
        )

    p50_raw, p90_raw = _heuristic_band(task_count=task_count, model=model)
    return CostBand(
        p50=_sanitize(p50_raw),
        p90=_sanitize(p90_raw),
        samples=0,
        cold_start=True,
        role=role_norm,
        adapter=adapter_norm,
        model=model,
    )


def format_band(band: CostBand) -> str:
    """Render a :class:`CostBand` for the preflight banner.

    Returns a string of the form
    ``"Estimated cost: p50 $X.XX, p90 $Y.YY"``, with ``" (cold estimate)"``
    appended on the cold-start path.
    """
    base = f"Estimated cost: p50 ${band.p50:.2f}, p90 ${band.p90:.2f}"
    if band.cold_start:
        return f"{base} {_COLD_START_LABEL}"
    return base
