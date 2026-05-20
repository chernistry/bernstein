"""GlitchTip insights fetcher for ``bernstein doctor glitchtip``.

Pulls last-24h issue counts by severity and last-7d trend, plus the top
unresolved issues ranked by event count. The module is pure data: it
returns dataclasses that the CLI layer renders.

Soft-fail policy
----------------
The fetcher returns an :class:`InsightsResult` with ``ok=False`` and a
human-readable ``reason`` when:

* ``BERNSTEIN_GLITCHTIP_TOKEN`` is not set (operator has not yet wired
  the API token)
* the configured base URL cannot be reached
* the API returns a non-2xx response

The CLI layer treats a soft-fail as a warning rather than a hard error
so that running ``bernstein doctor glitchtip`` on a fresh checkout does
not block any operator workflow.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Environment variable carrying the GlitchTip API token. Distinct from
#: ``BERNSTEIN_GLITCHTIP_DSN`` (used for runtime event submission) so
#: that operators can opt into the read-side surface without giving the
#: orchestrator process write credentials.
ENV_GLITCHTIP_TOKEN = "BERNSTEIN_GLITCHTIP_TOKEN"

#: Environment variable carrying the optional override for the base
#: URL. Defaults to the public bernstein.run endpoint so a typical
#: operator only needs to export the token.
ENV_GLITCHTIP_BASE_URL = "BERNSTEIN_GLITCHTIP_BASE_URL"

#: Environment variable carrying the GlitchTip organisation slug. Most
#: operators run a single org, so the default matches the bernstein.run
#: deployment.
ENV_GLITCHTIP_ORG = "BERNSTEIN_GLITCHTIP_ORG"

DEFAULT_BASE_URL = "https://errors.bernstein.run"
DEFAULT_ORG_SLUG = "bernstein"

#: Severity labels used by the Sentry-protocol API. Anything outside this
#: set is bucketed under ``other`` so the summary table stays narrow.
KNOWN_LEVELS: tuple[str, ...] = ("fatal", "error", "warning", "info", "debug")

#: Number of top unresolved issues surfaced by default.
DEFAULT_TOP_N = 5


@dataclass(frozen=True)
class GlitchTipIssue:
    """A single GlitchTip issue as surfaced to the operator."""

    short_id: str
    title: str
    level: str
    status: str
    count: int
    user_count: int
    first_seen: str
    last_seen: str
    permalink: str

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping for the ``--json`` flag."""
        return asdict(self)


@dataclass(frozen=True)
class InsightsResult:
    """Aggregate report returned by :func:`fetch_insights`."""

    ok: bool
    reason: str = ""
    base_url: str = ""
    org_slug: str = ""
    issues_24h: int = 0
    new_24h: int = 0
    severity_24h: dict[str, int] = field(default_factory=dict)
    trend_7d: list[int] = field(default_factory=list)
    top_unresolved: list[GlitchTipIssue] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping for the ``--json`` flag."""
        payload = asdict(self)
        payload["top_unresolved"] = [i.as_dict() for i in self.top_unresolved]
        return payload


def _read_env(
    env: dict[str, str] | None,
) -> tuple[str | None, str, str]:
    """Resolve token, base URL, and org slug from env with defaults.

    A ``None`` ``env`` argument means read ``os.environ``. The token is
    returned verbatim (``None`` when missing) so the caller can soft-fail
    with a precise reason string.
    """
    source = env if env is not None else dict(os.environ)
    token = source.get(ENV_GLITCHTIP_TOKEN) or None
    base_url = source.get(ENV_GLITCHTIP_BASE_URL) or DEFAULT_BASE_URL
    org_slug = source.get(ENV_GLITCHTIP_ORG) or DEFAULT_ORG_SLUG
    return token, base_url.rstrip("/"), org_slug


def _coerce_issue(raw: Any) -> GlitchTipIssue | None:
    """Convert a single API response row into a :class:`GlitchTipIssue`.

    Returns ``None`` for malformed rows so partial responses still
    surface the valid entries.
    """
    if not isinstance(raw, dict):
        return None
    try:
        count_raw = raw.get("count", 0)
        user_raw = raw.get("userCount", 0)
        return GlitchTipIssue(
            short_id=str(raw.get("shortId", "")),
            title=str(raw.get("title", ""))[:200],
            level=str(raw.get("level", "")).lower() or "unknown",
            status=str(raw.get("status", "unresolved")),
            count=int(count_raw) if str(count_raw).isdigit() else 0,
            user_count=int(user_raw) if str(user_raw).isdigit() else 0,
            first_seen=str(raw.get("firstSeen", "")),
            last_seen=str(raw.get("lastSeen", "")),
            permalink=str(raw.get("permalink", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def summarise_severity(issues: Iterable[GlitchTipIssue]) -> dict[str, int]:
    """Bucket issues by severity level.

    Unknown levels fall into the ``other`` bucket so the table stays
    narrow. All known buckets are present in the result (zero-filled)
    so renderers can iterate ``KNOWN_LEVELS`` without branching.
    """
    counts: dict[str, int] = {level: 0 for level in KNOWN_LEVELS}
    counts["other"] = 0
    for issue in issues:
        bucket = issue.level if issue.level in KNOWN_LEVELS else "other"
        counts[bucket] += issue.count
    return counts


def top_unresolved(
    issues: Iterable[GlitchTipIssue],
    limit: int = DEFAULT_TOP_N,
) -> list[GlitchTipIssue]:
    """Return the top ``limit`` unresolved issues by event count.

    Resolved / ignored issues are filtered out so the operator sees only
    actionable surfaces.
    """
    unresolved = [i for i in issues if i.status == "unresolved"]
    return sorted(unresolved, key=lambda i: (i.count, i.user_count), reverse=True)[:limit]


def _parse_iso8601(value: str) -> dt.datetime | None:
    """Parse an ISO8601 timestamp, returning ``None`` on bad input."""
    if not value:
        return None
    try:
        normalised = value.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalised)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed


def count_new_since(
    issues: Iterable[GlitchTipIssue],
    cutoff: dt.datetime,
) -> int:
    """Return the number of issues first seen at or after ``cutoff``.

    Used by the periodic nudge to surface only deltas since the last
    operator check.
    """
    n = 0
    for issue in issues:
        first = _parse_iso8601(issue.first_seen)
        if first is not None and first >= cutoff:
            n += 1
    return n


def _http_get(url: str, token: str, timeout: float) -> tuple[int, Any]:
    """Issue a GET against the GlitchTip API.

    Returns ``(status_code, parsed_json)``. JSON parse failures yield
    ``parsed_json = None`` so the caller can soft-fail with a precise
    reason. Imports ``httpx`` lazily so minimal installs do not pay the
    cost when this module is not used.
    """
    import httpx

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise GlitchTipHTTPError(str(exc)) from exc

    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


class GlitchTipHTTPError(RuntimeError):
    """Raised when the underlying HTTP call cannot complete."""


def fetch_insights(
    *,
    env: dict[str, str] | None = None,
    timeout: float = 5.0,
    http_get: Any = None,
    top_n: int = DEFAULT_TOP_N,
) -> InsightsResult:
    """Fetch insights from the GlitchTip API.

    Parameters
    ----------
    env:
        Optional mapping to read configuration from. Defaults to
        ``os.environ``. Useful for tests so they can pass a known
        configuration without touching process state.
    timeout:
        Per-request timeout in seconds.
    http_get:
        Optional callable matching :func:`_http_get` for dependency
        injection in tests. The callable receives ``(url, token,
        timeout)`` and returns ``(status_code, parsed_json)``.
    top_n:
        Number of top unresolved issues to surface.

    Returns
    -------
    InsightsResult
        ``ok=False`` with a reason string for any soft-fail; ``ok=True``
        with populated counts and a top-N list otherwise.
    """
    token, base_url, org_slug = _read_env(env)
    if not token:
        return InsightsResult(
            ok=False,
            reason=f"{ENV_GLITCHTIP_TOKEN} not set; cannot query GlitchTip API",
            base_url=base_url,
            org_slug=org_slug,
        )

    issues_url = f"{base_url}/api/0/organizations/{org_slug}/issues/?statsPeriod=24h&limit=100"
    trend_url = f"{base_url}/api/0/organizations/{org_slug}/issues/?statsPeriod=7d&limit=100"

    getter = http_get if http_get is not None else _http_get

    try:
        status_24h, payload_24h = getter(issues_url, token, timeout)
    except GlitchTipHTTPError as exc:
        return InsightsResult(
            ok=False,
            reason=f"GlitchTip API unreachable: {exc}",
            base_url=base_url,
            org_slug=org_slug,
        )

    if status_24h < 200 or status_24h >= 300:
        return InsightsResult(
            ok=False,
            reason=f"GlitchTip API returned HTTP {status_24h}",
            base_url=base_url,
            org_slug=org_slug,
        )
    if not isinstance(payload_24h, list):
        return InsightsResult(
            ok=False,
            reason="GlitchTip API returned a non-list payload for 24h issues",
            base_url=base_url,
            org_slug=org_slug,
        )

    issues_24h = [coerced for raw in payload_24h if (coerced := _coerce_issue(raw))]

    # Trend pull is best-effort -- if it fails we still surface the 24h
    # numbers rather than failing the whole report.
    trend_counts: list[int] = []
    try:
        status_7d, payload_7d = getter(trend_url, token, timeout)
        if 200 <= status_7d < 300 and isinstance(payload_7d, list):
            issues_7d = [coerced for raw in payload_7d if (coerced := _coerce_issue(raw))]
            trend_counts = _bucket_trend_by_day(issues_7d)
    except GlitchTipHTTPError:
        trend_counts = []

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    new_24h = count_new_since(issues_24h, cutoff)

    return InsightsResult(
        ok=True,
        base_url=base_url,
        org_slug=org_slug,
        issues_24h=len(issues_24h),
        new_24h=new_24h,
        severity_24h=summarise_severity(issues_24h),
        trend_7d=trend_counts,
        top_unresolved=top_unresolved(issues_24h, limit=top_n),
    )


def _bucket_trend_by_day(issues: Iterable[GlitchTipIssue]) -> list[int]:
    """Bucket a 7-day issue list into seven daily counts (oldest first).

    The GlitchTip API does not expose a per-day histogram in the issues
    endpoint, so we bucket by ``firstSeen`` ourselves. Issues without a
    parseable ``firstSeen`` are skipped.
    """
    now = dt.datetime.now(dt.UTC)
    buckets = [0] * 7
    for issue in issues:
        first = _parse_iso8601(issue.first_seen)
        if first is None:
            continue
        age_days = (now - first).days
        if 0 <= age_days < 7:
            buckets[6 - age_days] += issue.count
    return buckets
