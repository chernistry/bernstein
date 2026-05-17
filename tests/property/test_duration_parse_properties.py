"""Property tests for ``bernstein.core.preview.manager.parse_duration``.

The ``--expire`` flag on ``bernstein preview`` accepts compact duration
strings (``"30m"``, ``"4h"``, ``"3600"``). The parser is shared
machinery, but the input surface is user-supplied — adversarial values
are realistic. Properties:

* **Suffix arithmetic is correct** — ``"<n>m"`` always parses to
  ``n * 60``, ``"<n>h"`` to ``n * 3600``, etc. Catches off-by-one
  regressions in ``_DURATION_UNITS``.

* **Whitespace and casing are normalised** — ``"  30M  "`` parses
  identically to ``"30m"``. The production CLI strips and lowercases;
  this property locks the contract.

* **Zero / negative values are rejected** — the parser must raise
  ``ValueError`` rather than return ``0`` (which would mean
  "never-expiring" elsewhere and is a security footgun).

* **Numeric inputs (int / float / numeric string) agree** —
  ``parse_duration(60)`` == ``parse_duration("60")`` == 60. Catches
  type-dispatch regressions where the str branch handled ``"60s"``
  differently from the numeric branch handling ``60``.

* **Invalid suffixes raise** — anything outside ``[s, m, h, d]``
  surfaces ValueError. Catches over-permissive regex changes.

Hypothesis budget per property is the default smoke profile (50
examples); the parser is pure-Python and microsecond-fast, so no
deadline tuning is needed.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bernstein.core.preview.manager import parse_duration

# Suffix → seconds-per-unit lookup; the parser keeps the same map
# internally. We duplicate it here so any drift between source and
# tests is surfaced as a property failure (rather than passing because
# the test imports the constant).
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


@given(
    n=st.integers(min_value=1, max_value=10_000),
    unit=st.sampled_from(list(_UNIT_SECONDS)),
)
def test_suffix_arithmetic(n: int, unit: str) -> None:
    """``"<n><unit>"`` always parses to ``n * seconds_per_unit``."""
    assert parse_duration(f"{n}{unit}") == n * _UNIT_SECONDS[unit]


@given(n=st.integers(min_value=1, max_value=10_000))
def test_bare_number_is_seconds(n: int) -> None:
    """A bare digit string is interpreted as seconds.

    Catches a regression where the parser would default to a different
    unit (e.g. minutes) when the suffix is absent — which would silently
    extend or shrink every operator-supplied TTL by 60x.
    """
    assert parse_duration(str(n)) == n


@given(
    n=st.integers(min_value=1, max_value=10_000),
    unit=st.sampled_from(list(_UNIT_SECONDS)),
    pad_left=st.text(alphabet=" \t", max_size=4),
    pad_right=st.text(alphabet=" \t", max_size=4),
    uppercase=st.booleans(),
)
def test_whitespace_and_case_normalised(
    n: int,
    unit: str,
    pad_left: str,
    pad_right: str,
    uppercase: bool,
) -> None:
    """Whitespace and casing are stripped before parsing."""
    suffix = unit.upper() if uppercase else unit
    spec = f"{pad_left}{n}{suffix}{pad_right}"
    assert parse_duration(spec) == n * _UNIT_SECONDS[unit]


@given(n=st.integers(max_value=0))
def test_zero_or_negative_int_rejected(n: int) -> None:
    """Zero / negative integers must raise ``ValueError``.

    A returned ``0`` would mean "never expire" downstream in the share
    link issuer — a security regression that the parser must refuse to
    surface.
    """
    with pytest.raises(ValueError, match="duration must be > 0"):
        parse_duration(n)


@given(n=st.integers(min_value=1, max_value=10_000))
def test_int_and_str_agree(n: int) -> None:
    """``parse_duration(n)`` and ``parse_duration(str(n))`` agree."""
    assert parse_duration(n) == parse_duration(str(n))


@given(
    suffix=st.text(
        alphabet=st.characters(
            min_codepoint=ord("a"),
            max_codepoint=ord("z"),
            blacklist_characters=tuple(_UNIT_SECONDS),
        ),
        min_size=1,
        max_size=4,
    ),
    n=st.integers(min_value=1, max_value=100),
)
def test_invalid_suffix_raises(suffix: str, n: int) -> None:
    """Any non-``s/m/h/d`` suffix must raise ``ValueError``.

    The parser's regex is ``(\\d+)([smhd]?)``; this property guards
    against an accidental loosening to ``[a-z]*`` (which would silently
    treat ``"5x"`` as seconds and confuse operators).
    """
    with pytest.raises(ValueError, match="invalid duration spec"):
        parse_duration(f"{n}{suffix}")


@given(
    spec=st.one_of(
        st.text(min_size=1, max_size=8).filter(lambda s: not s.strip().isdigit()),
        st.just("-5m"),
        st.just("5.5m"),
    ),
)
def test_garbage_strings_reject_cleanly(spec: str) -> None:
    """Free-form garbage strings either parse correctly or raise.

    The contract is: never silently return a misleading value. Either
    the regex matches and the arithmetic is computed (in which case
    the returned int must be positive) or ``ValueError`` is raised.
    """
    try:
        result = parse_duration(spec)
    except ValueError:
        return
    assert isinstance(result, int)
    assert result > 0


def test_none_returns_default() -> None:
    """``None`` and empty string fall back to the default arg.

    Pinned via a regular unit-style assertion because the property is
    over a 2-element input space.
    """
    assert parse_duration(None, default=42) == 42
    assert parse_duration("", default=42) == 42
