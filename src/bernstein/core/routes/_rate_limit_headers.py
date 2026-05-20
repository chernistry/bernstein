"""Helpers for building 429 ``HTTPException`` instances with standard headers.

Per the ``[api-design]`` rate-limiting guidance, every 429 response must
include ``Retry-After`` so a client knows when to retry.  Where the
caller can also surface budget metadata (a ``RateLimitMeter`` snapshot,
the bucket reset epoch, the bucket capacity), the richer
``X-RateLimit-Limit`` / ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``
headers go on the same response.

These helpers exist so route handlers do not hand-roll the header dict
on every 429 raise site - the values must be HTTP-string-typed and
attached via the ``headers=`` kwarg of ``HTTPException`` so FastAPI
forwards them to the response.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from bernstein.adapters.base import RateLimitMeter


#: Fallback ``Retry-After`` for 429 responses where the caller has no
#: better signal (no ``RateLimitMeter``, no explicit reset epoch).
#: One minute is the conventional default for tenant-quota and
#: concurrent-session caps that reset on the next request boundary.
_DEFAULT_RETRY_AFTER_SECONDS: int = 60


def _retry_after_from_meter(meter: RateLimitMeter) -> int:
    """Return the ``Retry-After`` value (seconds) suggested by *meter*.

    Uses the meter's current advisory backoff when it is non-zero;
    otherwise falls back to the module default so the header is always
    populated.
    """
    backoff = float(getattr(meter, "backoff_seconds_current", 0.0) or 0.0)
    if backoff > 0:
        return max(1, math.ceil(backoff))
    return _DEFAULT_RETRY_AFTER_SECONDS


def rate_limit_exception(
    reason: str,
    *,
    meter: RateLimitMeter | None = None,
    retry_after_seconds: int | None = None,
    limit: int | None = None,
    remaining: int | None = None,
    reset_epoch: int | None = None,
) -> HTTPException:
    """Build a 429 ``HTTPException`` carrying standard rate-limit headers.

    ``Retry-After`` is always set.  If ``retry_after_seconds`` is given,
    that value wins.  Otherwise the meter (if any) is consulted for an
    advisory backoff, falling back to a module default.

    The richer headers (``X-RateLimit-Limit``, ``X-RateLimit-Remaining``,
    ``X-RateLimit-Reset``) are only set when the caller can supply real
    numbers.  Sending zero placeholders would lie to the client.

    Args:
        reason: Human-readable detail message returned in the JSON body.
        meter: Optional ``RateLimitMeter`` whose ``backoff_seconds_current``
            seeds ``Retry-After`` when no explicit value is given.
        retry_after_seconds: Explicit ``Retry-After`` value in seconds.
            Wins over the meter-derived default when set.
        limit: Optional bucket capacity for ``X-RateLimit-Limit``.
        remaining: Optional remaining budget for ``X-RateLimit-Remaining``.
        reset_epoch: Optional unix timestamp when the bucket resets,
            attached as ``X-RateLimit-Reset``.

    Returns:
        ``HTTPException(status_code=429)`` with the headers attached.
    """
    if retry_after_seconds is not None:
        retry_after = max(1, int(retry_after_seconds))
    elif meter is not None:
        retry_after = _retry_after_from_meter(meter)
    else:
        retry_after = _DEFAULT_RETRY_AFTER_SECONDS

    headers: dict[str, str] = {"Retry-After": str(retry_after)}

    if limit is not None:
        headers["X-RateLimit-Limit"] = str(int(limit))
    if remaining is not None:
        # Negative remaining would be a lie; clamp to zero.
        headers["X-RateLimit-Remaining"] = str(max(0, int(remaining)))
    if reset_epoch is not None:
        headers["X-RateLimit-Reset"] = str(int(reset_epoch))
    elif meter is not None and retry_after > 0:
        # Best-effort reset epoch when the caller did not supply one but
        # we know how long to back off.
        headers["X-RateLimit-Reset"] = str(int(time.time()) + retry_after)

    return HTTPException(status_code=429, detail=reason, headers=headers)


__all__ = ["rate_limit_exception"]
