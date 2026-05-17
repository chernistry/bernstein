"""Unit tests for :mod:`bernstein.core.skills.sanitizer`.

The sanitizer is a P1 security control: it strips invisible Unicode codepoints
from skill bodies before injection so a poisoned third-party skill cannot
smuggle prompt-injection instructions past the model.

Test density is intentionally high. Every documented branch of the spec has at
least one dedicated case, plus a battery of edge-cases, idempotency checks,
counter-emission assertions, and a regression sweep over the bundled skill
templates.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from bernstein.core.skills import sanitizer as sanitizer_module
from bernstein.core.skills.sanitizer import (
    is_sanitization_enabled,
    sanitize_skill_body,
    strip_invisible_tags,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

#: Invisible "HELLO" encoded in the Tag block (U+E0048 U+E0045 U+E004C ...).
INVISIBLE_HELLO = "\U000e0048\U000e0045\U000e004c\U000e004c\U000e004f"


@pytest.fixture(autouse=True)
def _clear_optout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to sanitizer-ON to avoid bleed-through."""
    monkeypatch.delenv("BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS", raising=False)


@pytest.fixture
def reset_counter() -> Iterator[None]:
    """Marker fixture for per-test counter delta assertions."""
    yield


def _counter_value(source_name: str) -> float:
    """Return the current value of the sanitization counter for *source_name*.

    Returns 0.0 if the label combination has never been observed or when
    ``prometheus_client`` is unavailable (stub fallback).
    """
    from bernstein.core.observability.prometheus import (
        skills_unicode_tags_stripped_total,
    )

    try:
        return float(
            skills_unicode_tags_stripped_total.labels(source_name=source_name)._value.get()  # type: ignore[attr-defined]
        )
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Spec-mandated cases (acceptance criteria)
# ---------------------------------------------------------------------------


def test_spec_invisible_hello_returns_count_5_and_empty_body() -> None:
    """Spec AC: invisible 'HELLO' returns count=5 and empty cleaned body."""
    cleaned, count = strip_invisible_tags(INVISIBLE_HELLO)
    assert cleaned == ""
    assert count == 5


def test_spec_clean_skill_returns_count_0_and_unchanged_body() -> None:
    """Spec AC: clean skill returns count=0 and unchanged body."""
    payload = "# Hello\nThis is a normal skill body with no invisibles.\n"
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == payload
    assert count == 0


# ---------------------------------------------------------------------------
# Mixed visible + invisible payloads
# ---------------------------------------------------------------------------


def test_mixed_visible_and_invisible_keeps_visible() -> None:
    payload = "hello" + INVISIBLE_HELLO + "world"
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == "helloworld"
    assert count == 5


def test_mixed_invisible_at_start_only() -> None:
    cleaned, count = strip_invisible_tags(INVISIBLE_HELLO + "world")
    assert cleaned == "world"
    assert count == 5


def test_mixed_invisible_at_end_only() -> None:
    cleaned, count = strip_invisible_tags("hello" + INVISIBLE_HELLO)
    assert cleaned == "hello"
    assert count == 5


def test_mixed_invisible_interleaved() -> None:
    payload = "h\U000e0001e\U000e0002l\U000e0003lo"
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == "hello"
    assert count == 3


def test_only_one_invisible_codepoint() -> None:
    cleaned, count = strip_invisible_tags("a\U000e0040b")
    assert cleaned == "ab"
    assert count == 1


# ---------------------------------------------------------------------------
# Tag block boundaries
# ---------------------------------------------------------------------------


def test_tag_block_lower_boundary() -> None:
    cleaned, count = strip_invisible_tags("\U000e0000")
    assert cleaned == ""
    assert count == 1


def test_tag_block_upper_boundary() -> None:
    cleaned, count = strip_invisible_tags("\U000e007f")
    assert cleaned == ""
    assert count == 1


def test_codepoint_just_above_tag_block_is_kept() -> None:
    # U+E0080: Private Use Area (Co), not Tag block. Should NOT be stripped.
    pua_above = "\U000e0080"
    cleaned, count = strip_invisible_tags(pua_above)
    if unicodedata.category(pua_above) == "Co":
        assert cleaned == pua_above
        assert count == 0


def test_entire_tag_block_is_stripped() -> None:
    payload = "".join(chr(cp) for cp in range(0xE0000, 0xE0080))
    assert len(payload) == 128
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == ""
    assert count == 128


# ---------------------------------------------------------------------------
# Interlinear annotation marks (U+FFF9-U+FFFB)
# ---------------------------------------------------------------------------


def test_interlinear_anchor_stripped() -> None:
    cleaned, count = strip_invisible_tags("a￹b")
    assert cleaned == "ab"
    assert count == 1


def test_interlinear_separator_stripped() -> None:
    cleaned, count = strip_invisible_tags("a￺b")
    assert cleaned == "ab"
    assert count == 1


def test_interlinear_terminator_stripped() -> None:
    cleaned, count = strip_invisible_tags("a￻b")
    assert cleaned == "ab"
    assert count == 1


def test_all_three_interlinear_marks_stripped() -> None:
    cleaned, count = strip_invisible_tags("￹￺￻")
    assert cleaned == ""
    assert count == 3


# ---------------------------------------------------------------------------
# Cf-category codepoints sampled across the BMP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "codepoint",
    [
        "​",  # ZERO WIDTH SPACE
        "‌",  # ZERO WIDTH NON-JOINER
        "‍",  # ZERO WIDTH JOINER
        "‎",  # LEFT-TO-RIGHT MARK
        "‏",  # RIGHT-TO-LEFT MARK
        "‪",  # LEFT-TO-RIGHT EMBEDDING
        "‫",  # RIGHT-TO-LEFT EMBEDDING
        "‬",  # POP DIRECTIONAL FORMATTING
        "‭",  # LEFT-TO-RIGHT OVERRIDE
        "‮",  # RIGHT-TO-LEFT OVERRIDE
        "⁠",  # WORD JOINER
        "⁡",  # FUNCTION APPLICATION
        "⁢",  # INVISIBLE TIMES
        "⁣",  # INVISIBLE SEPARATOR
        "⁤",  # INVISIBLE PLUS
        "⁪",  # INHIBIT SYMMETRIC SWAPPING
        "﻿",  # ZERO WIDTH NO-BREAK SPACE / BOM
        "­",  # SOFT HYPHEN
        "؀",  # ARABIC NUMBER SIGN
        "؜",  # ARABIC LETTER MARK
    ],
)
def test_cf_codepoint_is_stripped(codepoint: str) -> None:
    assert unicodedata.category(codepoint) == "Cf"
    cleaned, count = strip_invisible_tags(f"a{codepoint}b")
    assert cleaned == "ab"
    assert count == 1


def test_rtl_mark_is_stripped() -> None:
    """RTL marks live in Cf and must be stripped regardless of position."""
    payload = "Hello‏World"
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == "HelloWorld"
    assert count == 1


def test_bom_at_start_is_stripped() -> None:
    cleaned, count = strip_invisible_tags("﻿# Real content")
    assert cleaned == "# Real content"
    assert count == 1


# ---------------------------------------------------------------------------
# Visible codepoints stay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "plain ascii",
        "русский текст",
        "中文文本",
        "emoji rocket inline",
        "math: forall x in R",
        "tab\tand\nnewline",
        "quote: \"double\" 'single'",
        "code: `f(x) = x + 1`",
        "  leading and trailing  ",
        "\x00null-byte-allowed",
    ],
)
def test_visible_payload_unchanged(payload: str) -> None:
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == payload
    assert count == 0


def test_control_codes_not_stripped() -> None:
    """Cc-category control codes are NOT in scope.

    The sanitizer targets Cf, Tag block, and interlinear only. Control codes
    (Cc) have a different threat surface (terminal escapes) handled elsewhere.
    """
    payload = "\x07\x1b[31mred\x1b[0m"
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == payload
    assert count == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_string() -> None:
    cleaned, count = strip_invisible_tags("")
    assert cleaned == ""
    assert count == 0


def test_single_visible_char() -> None:
    cleaned, count = strip_invisible_tags("a")
    assert cleaned == "a"
    assert count == 0


def test_single_invisible_char() -> None:
    cleaned, count = strip_invisible_tags("\U000e0041")
    assert cleaned == ""
    assert count == 1


def test_all_invisible_long_string() -> None:
    payload = INVISIBLE_HELLO * 100
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == ""
    assert count == 500


def test_alternating_visible_invisible() -> None:
    payload = "".join("x\U000e0041" for _ in range(50))
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == "x" * 50
    assert count == 50


def test_very_long_clean_payload_unchanged() -> None:
    payload = "lorem ipsum dolor sit amet " * 1_000
    cleaned, count = strip_invisible_tags(payload)
    assert cleaned == payload
    assert count == 0


def test_return_type_is_tuple_of_str_and_int() -> None:
    result = strip_invisible_tags("hello")
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], str)
    assert isinstance(result[1], int)


def test_count_is_never_negative() -> None:
    for payload in ["", "a", INVISIBLE_HELLO, "​", "﻿bom"]:
        _, count = strip_invisible_tags(payload)
        assert count >= 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_on_clean_input() -> None:
    payload = "this is clean"
    once, count_once = strip_invisible_tags(payload)
    twice, count_twice = strip_invisible_tags(once)
    assert once == twice
    assert count_once == 0
    assert count_twice == 0


def test_idempotent_on_dirty_input() -> None:
    payload = "hello" + INVISIBLE_HELLO + "world"
    once, _ = strip_invisible_tags(payload)
    twice, count_twice = strip_invisible_tags(once)
    assert once == twice
    assert count_twice == 0


# ---------------------------------------------------------------------------
# UTF-8 round-trip
# ---------------------------------------------------------------------------


def test_utf8_roundtrip_preserves_cleaned_body() -> None:
    payload = "русский 中文 plain " + INVISIBLE_HELLO
    cleaned, _ = strip_invisible_tags(payload)
    encoded = cleaned.encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert decoded == cleaned


def test_no_invisible_bytes_in_cleaned_utf8_output() -> None:
    payload = "ascii" + INVISIBLE_HELLO + "more"
    cleaned, _ = strip_invisible_tags(payload)
    encoded = cleaned.encode("utf-8")
    # Tag block codepoints encode to 4 UTF-8 bytes starting with 0xF3 0xA0 0x80
    assert b"\xf3\xa0\x80" not in encoded
    assert b"\xf3\xa0\x81" not in encoded


# ---------------------------------------------------------------------------
# Opt-out (env var) behaviour
# ---------------------------------------------------------------------------


def test_is_sanitization_enabled_default() -> None:
    assert is_sanitization_enabled() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "on", "ON"])
def test_is_sanitization_enabled_opt_out(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS", value)
    assert is_sanitization_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "anything-else"])
def test_is_sanitization_enabled_other_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS", value)
    assert is_sanitization_enabled() is True


def test_sanitize_skill_body_respects_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS", "1")
    poisoned = "hello" + INVISIBLE_HELLO
    result = sanitize_skill_body(poisoned, skill_name="x", origin="y", source_name="z")
    assert result == poisoned


def test_sanitize_skill_body_default_strips() -> None:
    poisoned = "hello" + INVISIBLE_HELLO
    result = sanitize_skill_body(poisoned, skill_name="x", origin="y", source_name="z")
    assert result == "hello"


# ---------------------------------------------------------------------------
# sanitize_skill_body: WARN log + counter
# ---------------------------------------------------------------------------


def test_sanitize_skill_body_logs_warning_on_strip(caplog: pytest.LogCaptureFixture) -> None:
    poisoned = "ok" + INVISIBLE_HELLO
    with caplog.at_level(logging.WARNING, logger="bernstein.core.skills.sanitizer"):
        sanitize_skill_body(
            poisoned,
            skill_name="poison-skill",
            origin="/tmp/poison",
            source_name="evil-source",
        )
    assert any(
        "poison-skill" in rec.message and "evil-source" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_sanitize_skill_body_no_log_when_clean(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="bernstein.core.skills.sanitizer"):
        sanitize_skill_body("clean body", skill_name="ok", origin="/tmp/ok", source_name="local")
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warns == []


def test_sanitize_skill_body_counter_increments_per_source(reset_counter: None) -> None:
    before = _counter_value("test-source-a")
    sanitize_skill_body(
        INVISIBLE_HELLO + "x",
        skill_name="s1",
        origin="/a",
        source_name="test-source-a",
    )
    after = _counter_value("test-source-a")
    assert after - before == pytest.approx(5.0)


def test_sanitize_skill_body_counter_isolated_by_source(reset_counter: None) -> None:
    before_a = _counter_value("test-source-b1")
    before_b = _counter_value("test-source-b2")
    sanitize_skill_body(
        INVISIBLE_HELLO,
        skill_name="s",
        origin="/a",
        source_name="test-source-b1",
    )
    after_a = _counter_value("test-source-b1")
    after_b = _counter_value("test-source-b2")
    assert after_a - before_a == pytest.approx(5.0)
    assert after_b - before_b == pytest.approx(0.0)


def test_sanitize_skill_body_counter_not_incremented_on_clean(reset_counter: None) -> None:
    before = _counter_value("test-source-c")
    sanitize_skill_body("clean", skill_name="s", origin="/a", source_name="test-source-c")
    after = _counter_value("test-source-c")
    assert after - before == pytest.approx(0.0)


def test_sanitize_skill_body_returns_cleaned_string_on_hit() -> None:
    result = sanitize_skill_body(
        "x" + INVISIBLE_HELLO + "y",
        skill_name="s",
        origin="o",
        source_name="src",
    )
    assert result == "xy"


def test_sanitize_skill_body_returns_unchanged_on_clean() -> None:
    payload = "perfectly fine"
    result = sanitize_skill_body(payload, skill_name="s", origin="o", source_name="src")
    assert result == payload


# ---------------------------------------------------------------------------
# Regression: bundled skill templates must all be clean
# ---------------------------------------------------------------------------


def _iter_template_skill_files() -> list[Path]:
    templates_root = Path(__file__).resolve().parents[3] / "templates" / "skills"
    if not templates_root.is_dir():
        return []
    return sorted(templates_root.rglob("*.md"))


def test_regression_bundled_skills_have_zero_invisible_codepoints() -> None:
    """Every shipped skill template must sanitize with count=0."""
    files = _iter_template_skill_files()
    assert files, "expected at least one bundled skill template"
    dirty: list[tuple[Path, int]] = []
    for path in files:
        body = path.read_text(encoding="utf-8")
        _, count = strip_invisible_tags(body)
        if count > 0:
            dirty.append((path, count))
    assert not dirty, f"bundled templates contain invisible codepoints: {dirty}"


@pytest.mark.parametrize("path", _iter_template_skill_files(), ids=lambda p: p.name)
def test_regression_each_bundled_skill_clean(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    cleaned, count = strip_invisible_tags(body)
    assert count == 0, f"{path.name} contains {count} invisible codepoints"
    assert cleaned == body


# ---------------------------------------------------------------------------
# Module-level sanity
# ---------------------------------------------------------------------------


def test_public_api_exports() -> None:
    assert "strip_invisible_tags" in sanitizer_module.__all__
    assert "sanitize_skill_body" in sanitizer_module.__all__
    assert "is_sanitization_enabled" in sanitizer_module.__all__


def test_env_var_constant_is_documented() -> None:
    """Constant matches spec wording, so a typo cannot disable the CLI wiring."""
    assert sanitizer_module._OPT_OUT_ENV == "BERNSTEIN_UNSAFE_ALLOW_UNICODE_TAGS"
