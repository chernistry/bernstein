"""Property-based tests for the skill-pack invisible-Unicode sanitizer.

Invariants under test:

* **Idempotence.** ``strip(strip(x)) == strip(x)``.
* **No extension.** Output length never exceeds input length.
* **UTF-8 round-trip.** Cleaned output encodes and decodes without loss.
* **Count consistency.** ``count + len(cleaned) == len(input)``.
* **Mask correctness.** No invisible codepoint survives a single pass.
* **Cleanliness preservation.** Already-clean inputs are returned untouched.

Hypothesis exercises both adversarial Unicode (Tag block + Cf + RTL marks)
and ordinary text payloads.
"""

from __future__ import annotations

import unicodedata

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from bernstein.core.skills.sanitizer import strip_invisible_tags

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_tag_block = st.builds(chr, st.integers(min_value=0xE0000, max_value=0xE007F))
_interlinear = st.builds(chr, st.integers(min_value=0xFFF9, max_value=0xFFFB))

_cf_chars = st.sampled_from(
    [
        "​",  # ZWSP
        "‌",  # ZWNJ
        "‍",  # ZWJ
        "‎",  # LRM
        "‏",  # RLM
        "‪",  # LRE
        "‫",  # RLE
        "‬",  # PDF
        "‭",  # LRO
        "‮",  # RLO
        "⁠",  # WJ
        "⁡",  # FUNCTION APPLICATION
        "⁢",  # INVISIBLE TIMES
        "⁣",  # INVISIBLE SEPARATOR
        "⁤",  # INVISIBLE PLUS
        "⁪",  # INHIBIT SYMMETRIC SWAPPING
        "﻿",  # BOM
        "­",  # SOFT HYPHEN
    ]
)

# Visible text: exclude every category the sanitizer would strip and avoid
# lone surrogates which Python strings cannot contain.
_visible_text = st.text(
    st.characters(
        blacklist_categories=("Cs", "Cf"),
        max_codepoint=0xFFFF,
    ),
    min_size=0,
    max_size=128,
).filter(lambda s: not any(0xE0000 <= ord(c) <= 0xE007F or 0xFFF9 <= ord(c) <= 0xFFFB for c in s))

_invisible_glyph = st.one_of(_tag_block, _interlinear, _cf_chars)

_adversarial_payload = st.lists(
    st.one_of(_visible_text, _invisible_glyph),
    min_size=0,
    max_size=20,
).map("".join)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_idempotent(payload: str) -> None:
    once, _ = strip_invisible_tags(payload)
    twice, count_twice = strip_invisible_tags(once)
    assert once == twice
    assert count_twice == 0


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_never_extends(payload: str) -> None:
    cleaned, _ = strip_invisible_tags(payload)
    assert len(cleaned) <= len(payload)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_count_consistency(payload: str) -> None:
    cleaned, count = strip_invisible_tags(payload)
    assert count == len(payload) - len(cleaned)
    assert count >= 0


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_utf8_roundtrip(payload: str) -> None:
    cleaned, _ = strip_invisible_tags(payload)
    decoded = cleaned.encode("utf-8").decode("utf-8")
    assert decoded == cleaned


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_no_invisible_survives(payload: str) -> None:
    cleaned, _ = strip_invisible_tags(payload)
    for ch in cleaned:
        cp = ord(ch)
        assert not (0xE0000 <= cp <= 0xE007F), f"tag block char survived: U+{cp:X}"
        assert not (0xFFF9 <= cp <= 0xFFFB), f"interlinear char survived: U+{cp:X}"
        assert unicodedata.category(ch) != "Cf", f"Cf char survived: U+{cp:X}"


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_visible_text)
def test_property_clean_input_unchanged(payload: str) -> None:
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == payload
    assert count == 0


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(_invisible_glyph, min_size=0, max_size=64).map("".join))
def test_property_all_invisible_input_yields_empty(payload: str) -> None:
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == ""
    assert count == len(payload)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_visible_text, _visible_text, _invisible_glyph)
def test_property_invisible_glyph_always_removed(
    left: str,
    right: str,
    glyph: str,
) -> None:
    payload = left + glyph + right
    cleaned, count = strip_invisible_tags(payload)
    assert glyph not in cleaned
    assert count >= 1


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload, _adversarial_payload)
def test_property_concat_commutes_with_strip(payload_a: str, payload_b: str) -> None:
    """``strip(a+b)`` produces the same result as ``strip(a)+strip(b)``."""
    joint, _ = strip_invisible_tags(payload_a + payload_b)
    parts_a, _ = strip_invisible_tags(payload_a)
    parts_b, _ = strip_invisible_tags(payload_b)
    assert joint == parts_a + parts_b


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_count_equals_invisible_count(payload: str) -> None:
    """The reported count must equal the number of invisible chars in input."""
    expected = sum(
        1
        for ch in payload
        if (0xE0000 <= ord(ch) <= 0xE007F or 0xFFF9 <= ord(ch) <= 0xFFFB or unicodedata.category(ch) == "Cf")
    )
    _, count = strip_invisible_tags(payload)
    assert count == expected


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_visible_text)
def test_property_visible_text_preserves_codepoint_order(payload: str) -> None:
    cleaned, _ = strip_invisible_tags(payload)
    assert cleaned == "".join(
        c
        for c in payload
        if not (0xE0000 <= ord(c) <= 0xE007F or 0xFFF9 <= ord(c) <= 0xFFFB or unicodedata.category(c) == "Cf")
    )


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_cleaned_is_str_count_is_int(payload: str) -> None:
    cleaned, count = strip_invisible_tags(payload)
    assert isinstance(cleaned, str)
    assert isinstance(count, int)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_visible_text)
def test_property_visible_count_is_zero(payload: str) -> None:
    _, count = strip_invisible_tags(payload)
    assert count == 0


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(_tag_block, min_size=1, max_size=32).map("".join))
def test_property_tag_block_only_input_strips_completely(payload: str) -> None:
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == ""
    assert count == len(payload)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(_adversarial_payload)
def test_property_cleaned_length_matches_filter(payload: str) -> None:
    """``len(cleaned)`` equals the number of visible codepoints in the input."""
    visible_count = sum(
        1
        for ch in payload
        if not (0xE0000 <= ord(ch) <= 0xE007F or 0xFFF9 <= ord(ch) <= 0xFFFB or unicodedata.category(ch) == "Cf")
    )
    cleaned, _ = strip_invisible_tags(payload)
    assert len(cleaned) == visible_count
