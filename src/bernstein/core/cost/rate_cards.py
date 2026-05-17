"""Model rate cards for cost preflight.

This module is the single edit point for model pricing used by the
preflight estimator (see :mod:`bernstein.core.cost.preflight`). Updating
prices here must not require touching estimator code.

Values are blended ``USD per 1k tokens`` (input + output averaged), which
matches the granularity needed by the cold-start heuristic. Per-token-type
breakdowns are intentionally out of scope; see the dashboard ticket.

Rates are sourced from :data:`bernstein.core.cost.cost._MODEL_COST_USD_PER_1K`
to avoid drift; that table remains the canonical pricing dump.  The wrapper
here exists so callers don't reach into a private symbol and so we can swap
the source out (e.g. a YAML override) without touching estimator code.
"""

from __future__ import annotations

# ``_MODEL_COST_USD_PER_1K`` is the package-canonical rate dump; the leading
# underscore signals "do not edit ad-hoc -- go through this module instead",
# not "do not import". The cost sub-package ``__init__`` re-exports the name
# explicitly for callers like this one.
from bernstein.core.cost import _MODEL_COST_USD_PER_1K  # pyright: ignore[reportPrivateUsage]

__all__ = [
    "MissingRateCardError",
    "lookup_rate_per_1k",
    "rate_card",
]


class MissingRateCardError(KeyError):
    """Raised when no rate card matches the requested model name."""

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.model = model

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"no rate card for model {self.model!r}"


def rate_card() -> dict[str, float]:
    """Return a snapshot of the blended ``USD per 1k token`` rate card.

    Returns:
        Mapping of ``model substring -> blended USD/1k tokens``. The keys are
        matched by left-to-right substring against the requested model name
        in :func:`lookup_rate_per_1k`, so longer/more specific keys come
        first in the source table.
    """
    return dict(_MODEL_COST_USD_PER_1K)


def lookup_rate_per_1k(model: str, *, strict: bool = False) -> float:
    """Return blended USD/1k tokens for ``model``.

    Args:
        model: Model identifier (e.g. ``"sonnet"``, ``"gpt-5-mini"``).
        strict: When True, raise :class:`MissingRateCardError` on miss.
            When False, return ``0.005`` (matches the legacy fallback in
            :func:`bernstein.core.cost.cost._model_cost`).

    Returns:
        Blended cost per 1k tokens in USD.

    Raises:
        MissingRateCardError: When ``strict`` and no entry matches.
    """
    model_lower = model.lower()
    for key, cost in _MODEL_COST_USD_PER_1K.items():
        if key in model_lower:
            return cost
    if strict:
        raise MissingRateCardError(model)
    return 0.005
