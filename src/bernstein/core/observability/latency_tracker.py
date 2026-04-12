"""Provider API latency tracker with historical percentile charts (#674).

Records per-provider/model API latency samples with HTTP status codes, computes
sliding-window percentiles (p50/p95/p99), and detects provider degradation when
current p99 exceeds the historical baseline by a configurable multiplier.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default retention window in hours for timestamp-based eviction.
_DEFAULT_RETENTION_HOURS: int = 72

# Minimum samples required before percentile stats are meaningful.
_MIN_SAMPLES: int = 2


@dataclass(frozen=True)
class LatencySample:
    """A single recorded API latency observation.

    Attributes:
        provider: Provider name (e.g., ``"anthropic"``).
        model: Model identifier (e.g., ``"claude-sonnet-4-6"``).
        latency_ms: Observed response latency in milliseconds.
        timestamp: Unix epoch seconds when the sample was recorded.
        status_code: HTTP status code returned by the provider.
    """

    provider: str
    model: str
    latency_ms: float
    timestamp: float
    status_code: int


@dataclass(frozen=True)
class LatencyPercentiles:
    """Computed latency percentiles for a provider+model over a time period.

    Attributes:
        provider: Provider name.
        model: Model identifier.
        p50_ms: 50th-percentile (median) latency in milliseconds.
        p95_ms: 95th-percentile latency in milliseconds.
        p99_ms: 99th-percentile latency in milliseconds.
        sample_count: Number of samples used for computation.
        period_hours: Time window (in hours) over which percentiles were computed.
    """

    provider: str
    model: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    sample_count: int
    period_hours: float


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute a percentile from pre-sorted values.

    Args:
        sorted_values: Non-empty list of values sorted ascending.
        p: Percentile as a fraction in ``[0.0, 1.0]``.

    Returns:
        The percentile value.
    """
    idx = int(p * (len(sorted_values) - 1))
    return sorted_values[min(idx, len(sorted_values) - 1)]


class LatencyTracker:
    """In-memory provider API latency tracker with percentile statistics.

    Stores :class:`LatencySample` entries in a :class:`~collections.deque` per
    provider+model key. Older samples are evicted based on ``retention_hours``
    each time new data is recorded.

    Args:
        retention_hours: How many hours of samples to keep in memory.
    """

    def __init__(self, retention_hours: int = _DEFAULT_RETENTION_HOURS) -> None:
        self._retention_hours = retention_hours
        # Keyed by "provider:model" -> deque of LatencySample
        self._samples: dict[str, deque[LatencySample]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        provider: str,
        model: str,
        latency_ms: float,
        status_code: int = 200,
    ) -> None:
        """Record an API latency sample.

        Args:
            provider: Provider name (e.g., ``"anthropic"``).
            model: Model identifier (e.g., ``"claude-sonnet-4-6"``).
            latency_ms: Observed response latency in milliseconds.
            status_code: HTTP status code returned by the provider.
        """
        sample = LatencySample(
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            timestamp=time.time(),
            status_code=status_code,
        )
        key = _make_key(provider, model)
        with self._lock:
            if key not in self._samples:
                self._samples[key] = deque()
            self._samples[key].append(sample)
            self._evict(key)

    def get_percentiles(
        self,
        provider: str,
        model: str,
        hours: float = 24,
    ) -> LatencyPercentiles:
        """Compute latency percentiles over a time window.

        Args:
            provider: Provider name.
            model: Model identifier.
            hours: Number of hours to look back (default 24).

        Returns:
            :class:`LatencyPercentiles` for the requested window. If fewer
            than :data:`_MIN_SAMPLES` samples exist, percentiles are ``0.0``.
        """
        key = _make_key(provider, model)
        cutoff = time.time() - hours * 3600

        with self._lock:
            samples = self._samples.get(key)
            if samples is None:
                return LatencyPercentiles(
                    provider=provider,
                    model=model,
                    p50_ms=0.0,
                    p95_ms=0.0,
                    p99_ms=0.0,
                    sample_count=0,
                    period_hours=hours,
                )

            values = sorted(s.latency_ms for s in samples if s.timestamp >= cutoff)

        if len(values) < _MIN_SAMPLES:
            return LatencyPercentiles(
                provider=provider,
                model=model,
                p50_ms=values[0] if values else 0.0,
                p95_ms=values[0] if values else 0.0,
                p99_ms=values[0] if values else 0.0,
                sample_count=len(values),
                period_hours=hours,
            )

        return LatencyPercentiles(
            provider=provider,
            model=model,
            p50_ms=_percentile(values, 0.50),
            p95_ms=_percentile(values, 0.95),
            p99_ms=_percentile(values, 0.99),
            sample_count=len(values),
            period_hours=hours,
        )

    @staticmethod
    def detect_degradation(
        current_p99: float,
        historical_p99: float,
        threshold: float = 2.0,
    ) -> bool:
        """Detect whether a provider's latency has degraded.

        Args:
            current_p99: Current 99th-percentile latency in milliseconds.
            historical_p99: Historical baseline 99th-percentile latency.
            threshold: Multiplier above which degradation is flagged.

        Returns:
            ``True`` if ``current_p99 >= historical_p99 * threshold``, meaning
            the provider's tail latency has degraded beyond the acceptable
            multiplier. Returns ``False`` when ``historical_p99`` is zero or
            negative (no valid baseline).
        """
        if historical_p99 <= 0:
            return False
        return current_p99 >= historical_p99 * threshold

    def get_all_stats(self) -> list[LatencyPercentiles]:
        """Get percentile statistics for every tracked provider+model pair.

        Uses the full retention window for each pair.

        Returns:
            List of :class:`LatencyPercentiles`, one per provider+model.
        """
        with self._lock:
            keys = list(self._samples.keys())

        results: list[LatencyPercentiles] = []
        for key in keys:
            provider, model = _split_key(key)
            results.append(self.get_percentiles(provider, model, hours=self._retention_hours))
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict(self, key: str) -> None:
        """Remove samples older than the retention window.

        Must be called with ``self._lock`` held.

        Args:
            key: The provider:model key to evict stale samples from.
        """
        cutoff = time.time() - self._retention_hours * 3600
        dq = self._samples.get(key)
        if dq is None:
            return
        while dq and dq[0].timestamp < cutoff:
            dq.popleft()


# ------------------------------------------------------------------
# Key helpers
# ------------------------------------------------------------------


def _make_key(provider: str, model: str) -> str:
    """Build a composite key from provider and model."""
    return f"{provider}:{model}"


def _split_key(key: str) -> tuple[str, str]:
    """Split a composite key back into provider and model."""
    parts = key.split(":", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return key, ""
