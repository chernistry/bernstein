"""Property tests for ISO-8601 timestamp parsing helpers.

Several modules parse operator-supplied or self-emitted ISO timestamps:

* ``bernstein.core.security.article12_bundle._parse_iso`` — accepts
  ``"Z"``-suffixed strings (the audit log's emission format) by
  normalising to ``"+00:00"`` before delegation to
  ``datetime.fromisoformat``.

* ``bernstein.core.agents.heartbeat`` — same normalisation pattern.

* ``bernstein.core.orchestration.run_changelog`` — same.

A regression in any of these (dropped ``Z`` handling, accidental
``%S`` truncation) silently shifts timestamps by hours or rejects
the audit log's own output. The properties here pin the contract:

* **``Z`` suffix is equivalent to ``+00:00``** for every UTC instant.
* **Round-trip through ``isoformat`` + ``_parse_iso`` is the identity**.
* **The audit log's literal emission format parses cleanly**.
* **Timezone-aware datetimes survive the parser without dropping
  tzinfo** — a regression would interpret persisted UTC as local
  time, an hours-magnitude bug.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from bernstein.core.security.article12_bundle import _parse_iso

# Restrict to a wide but reasonable epoch window so Hypothesis explores
# DST boundaries, leap days, and unusual months without ever hitting
# the ``datetime.MAX`` overflow or the pre-1970 zoneinfo gaps.
_DATETIMES = st.datetimes(
    min_value=datetime(1990, 1, 1),
    max_value=datetime(2099, 12, 31, 23, 59, 59),
    timezones=st.just(UTC),
)


@given(dt=_DATETIMES)
def test_z_suffix_equals_plus_zero(dt: datetime) -> None:
    """``"...Z"`` parses to the same instant as ``"...+00:00"``.

    The audit log emits its timestamps with a literal ``Z`` suffix;
    if ``_parse_iso`` ever dropped the normalisation step, every
    audit-export bundle generation would shift those timestamps to
    naive (local-zone-interpreted) datetimes — a silent hours-scale
    drift in compliance reports.
    """
    iso_z = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    iso_plus = dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    assert _parse_iso(iso_z) == _parse_iso(iso_plus)


@given(dt=_DATETIMES)
def test_round_trip_isoformat(dt: datetime) -> None:
    """``_parse_iso(dt.isoformat())`` equals ``dt``.

    Catches regressions where the parser drops microseconds (which
    would shift up to a second per parse — too small for unit tests
    to notice but a real bug in chronological event ordering).
    """
    iso = dt.isoformat()
    parsed = _parse_iso(iso)
    assert parsed == dt


@given(dt=_DATETIMES)
def test_audit_log_emission_format_parses(dt: datetime) -> None:
    """The exact format the audit log writes is parseable.

    The audit log produces ``%Y-%m-%dT%H:%M:%S.%fZ``. If the parser
    couldn't accept that format, every audit export bundle generation
    would crash on its own input — a CI-time regression worth catching
    before it merges.
    """
    audit_format = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    parsed = _parse_iso(audit_format)
    assert parsed.tzinfo is not None  # tzinfo preserved
    assert parsed.replace(microsecond=dt.microsecond) == dt


@given(
    dt=_DATETIMES,
    offset_hours=st.integers(min_value=-12, max_value=14),
)
def test_nonzero_offset_preserved(dt: datetime, offset_hours: int) -> None:
    """Non-UTC offsets survive the parser without being normalised away.

    Operator-supplied timestamps may come from any tz; if the parser
    normalised everything to UTC, downstream ``< since`` /
    ``< until`` comparisons against UTC-emitted audit lines would
    misclassify events near the boundary.
    """
    tz = timezone(timedelta(hours=offset_hours))
    shifted = dt.astimezone(tz)
    iso = shifted.isoformat()
    parsed = _parse_iso(iso)
    assert parsed == shifted
    # The instant is the same; the tzinfo may be normalised — only
    # the absolute UTC moment is the load-bearing contract.
    assert parsed.utcoffset() == timedelta(hours=offset_hours)


@given(dt=_DATETIMES)
def test_seconds_precision_round_trip(dt: datetime) -> None:
    """Round-trip without microseconds still preserves the instant.

    The audit log strips microseconds for some compatibility paths
    (e.g. archive subdir naming). The parser must accept that shorter
    format cleanly.
    """
    truncated = dt.replace(microsecond=0)
    iso = truncated.strftime("%Y-%m-%dT%H:%M:%SZ")
    parsed = _parse_iso(iso)
    assert parsed == truncated


@given(dt=_DATETIMES)
def test_two_calls_idempotent(dt: datetime) -> None:
    """Parsing the same value twice yields equal datetimes.

    Trivially true for ``datetime.fromisoformat`` but a regression
    that introduced mutable state (e.g. a memoised parser) could
    surface here on a second call returning a wrapped or
    cache-corrupted value.
    """
    iso = dt.isoformat()
    a = _parse_iso(iso)
    b = _parse_iso(iso)
    assert a == b
