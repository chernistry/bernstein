"""Prometheus telemetry for the promptware detector.

Exposes a single histogram - ``bernstein_security_promptware_score`` -
labelled by adapter, tool, and size bucket. The histogram lives in the
existing dedicated registry under :mod:`bernstein.core.observability.prometheus`
so the standard ``/metrics`` endpoint picks it up automatically.

Cardinality is guarded: adapter and tool names that look unfamiliar
(non-alphanumeric, longer than 32 chars) collapse to ``"other"`` so a
malicious tool output cannot blow up the label set.
"""

from __future__ import annotations

import re
from typing import Any, Final

from bernstein.core.observability.prometheus import Histogram as _Histogram
from bernstein.core.observability.prometheus import registry

_Histogram_Any: Any = _Histogram

__all__ = [
    "PROMPTWARE_SCORE_BUCKETS",
    "observe_score",
    "promptware_score",
]


# Buckets are score thresholds in [0, 1]. We use ten buckets so a
# scrape returns a fine-grained histogram even when the detector mostly
# sees benign output.
PROMPTWARE_SCORE_BUCKETS: Final[tuple[float, ...]] = (
    0.05,
    0.1,
    0.2,
    0.3,
    0.5,
    0.7,
    0.8,
    0.9,
    0.95,
    1.0,
)

promptware_score: Any = _Histogram_Any(
    "bernstein_security_promptware_score",
    "Per-output promptware detector score, by adapter, tool, and size bucket.",
    buckets=PROMPTWARE_SCORE_BUCKETS,
    labelnames=["adapter", "tool", "bucket"],
    registry=registry,
)


_KNOWN_BUCKETS: Final[frozenset[str]] = frozenset({"tiny", "small", "medium", "large"})

_LABEL_RX: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")


def _safe_label(value: str) -> str:
    """Collapse hostile labels to ``"other"`` to bound cardinality."""
    if not value:
        return "unknown"
    return value if _LABEL_RX.match(value) else "other"


def observe_score(
    value: float,
    *,
    adapter: str,
    tool: str,
    bucket: str,
) -> None:
    """Record one detector score observation.

    Args:
        value: Score in ``[0.0, 1.0]``. Out-of-range values are clamped.
        adapter: Originating CLI adapter (e.g. ``"claude"``). Sanitised.
        tool: Tool name (e.g. ``"WebFetch"``). Sanitised.
        bucket: One of ``"tiny"``, ``"small"``, ``"medium"``, ``"large"``.
            Unknown values collapse to ``"unknown"``.
    """
    clamped = max(0.0, min(1.0, float(value)))
    bucket_label = bucket if bucket in _KNOWN_BUCKETS else "unknown"
    promptware_score.labels(
        adapter=_safe_label(adapter),
        tool=_safe_label(tool),
        bucket=bucket_label,
    ).observe(clamped)
