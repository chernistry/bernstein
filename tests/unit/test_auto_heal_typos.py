"""Unit tests for ``scripts/auto_heal_typos.py``.

The extractor decides which failing-token candidates the auto-heal
workflow will silently allowlist into ``typos.toml``. A wrong "yes" lets
a real prose typo slip in; a wrong "no" leaves CI red. The vendor-field
shape filter is therefore the safety boundary.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from unittest import mock

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_SPEC = importlib.util.spec_from_file_location("auto_heal_typos", _SCRIPTS / "auto_heal_typos.py")
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

extract_candidates = _MOD.extract_candidates
is_vendor_field_shape = _MOD.is_vendor_field_shape
main = _MOD.main


# ---- is_vendor_field_shape -------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "noteable_type",  # snake_case with underscore
        "noteable_id",  # snake_case with underscore
        "stmt_descr",  # short but underscore present
        "unparseable",  # 11 chars, all letters, long enough
        "subprocessor",  # 12 chars, long enough
        "ipv4_addr",  # digit + underscore
        "k8s",  # 3 chars with digit (shape: yes; will reject by length too)
    ],
)
def test_vendor_field_shape_accepts_legit_field_names(token: str) -> None:
    # "k8s" is borderline (3 chars w/ digit) -- our rule says digit OR
    # length > 7. With a digit it qualifies.
    assert is_vendor_field_shape(token) is True


@pytest.mark.parametrize(
    "token",
    [
        "recieve",
        "occured",
        "seperate",
        "definately",
        "calender",
        "wierd",
    ],
)
def test_vendor_field_shape_rejects_common_prose_typos(token: str) -> None:
    # Even if the shape would pass, the denylist forces "no".
    assert is_vendor_field_shape(token) is False


@pytest.mark.parametrize(
    "token",
    [
        "color",  # 5 chars, no digit / underscore -> reject (prose-like)
        "teh",  # short prose typo
        "form",  # short prose word
        "data",  # short prose word
    ],
)
def test_vendor_field_shape_rejects_short_prose_words(token: str) -> None:
    assert is_vendor_field_shape(token) is False


@pytest.mark.parametrize(
    "token",
    [
        "ab",  # too short (< 3)
        "x" * 41,  # too long (> 40)
        "1abc",  # starts with digit
        "_abc",  # starts with underscore
        "Noteable",  # uppercase letter
        "note-able",  # hyphen
        "note.able",  # dot
        "note able",  # space
        "",  # empty
    ],
)
def test_vendor_field_shape_rejects_bad_shape(token: str) -> None:
    assert is_vendor_field_shape(token) is False


def test_vendor_field_shape_accepts_long_pure_letters() -> None:
    # 8+ chars, all letters: accept (vendor names like "unparseable").
    assert is_vendor_field_shape("eightchr") is True


def test_vendor_field_shape_rejects_seven_char_letters_only() -> None:
    # 7 chars, all letters, no digit / underscore: rejected (could be prose).
    assert is_vendor_field_shape("sevench") is False


# ---- extract_candidates ----------------------------------------------------


_SAMPLE_LOG = """\
error: `noteable` should be `notable`
  --> src/foo.py:1:1
error: `noteable_id` should be `notable_id`
  --> src/foo.py:1:1
error: `recieve` should be `receive`
  --> src/bar.py:5:5
error: `unparseable` should be `unparsable`
  --> src/baz.py:7:7
"""


def test_extract_candidates_picks_vendor_shapes_only() -> None:
    candidates = extract_candidates(_SAMPLE_LOG)
    # `noteable` is 8 letters with no digit / underscore -> shape accepts
    # via length > 7. `noteable_id` has underscore -> accepts. `recieve`
    # is on the denylist -> rejected. `unparseable` is 11 chars, accepts.
    assert "noteable" in candidates
    assert "noteable_id" in candidates
    assert "unparseable" in candidates
    assert "recieve" not in candidates


def test_extract_candidates_deduplicates() -> None:
    log = "error: `noteable` should be `notable`\nerror: `noteable` should be `notable`\n"
    candidates = extract_candidates(log)
    assert candidates == ["noteable"]


def test_extract_candidates_preserves_first_seen_order() -> None:
    log = (
        "error: `unparseable` should be `unparsable`\n"
        "error: `noteable` should be `notable`\n"
        "error: `noteable_id` should be `notable_id`\n"
    )
    assert extract_candidates(log) == ["unparseable", "noteable", "noteable_id"]


def test_extract_candidates_empty_log() -> None:
    assert extract_candidates("") == []


def test_extract_candidates_no_matches() -> None:
    log = "Some unrelated output\nWith no typos format\n"
    assert extract_candidates(log) == []


def test_extract_candidates_ignores_prose_typo_even_if_extracted() -> None:
    log = "error: `recieve` should be `receive`\n"
    assert extract_candidates(log) == []


def test_extract_candidates_handles_mixed_legit_and_prose() -> None:
    log = (
        "error: `recieve` should be `receive`\n"
        "error: `noteable_type` should be `notable_type`\n"
        "error: `occured` should be `occurred`\n"
    )
    assert extract_candidates(log) == ["noteable_type"]


def test_extract_candidates_full_typos_v1_45_format() -> None:
    # Real-world v1.45 layout uses fancy box-drawing chars; the regex
    # anchors on the first line of each block so the rest is harmless.
    log = (
        "error: `noteable` should be `notable`\n"
        "  ╭▸ \n"
        "1 │ ./tests/integration/gitlab/fixtures/note_mr.json\n"
        "  ╰╴                            ━━━━━━━━\n"
    )
    assert extract_candidates(log) == ["noteable"]


# ---- main() ----------------------------------------------------------------


def _run_main(stdin_text: str) -> tuple[int, str]:
    stdin = io.StringIO(stdin_text)
    stdout = io.StringIO()
    with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout):
        rc = main()
    return rc, stdout.getvalue()


def test_main_returns_zero_on_empty_input() -> None:
    rc, out = _run_main("")
    assert rc == 0
    assert out == ""


def test_main_prints_candidates_newline_separated() -> None:
    _rc, out = _run_main(_SAMPLE_LOG)
    lines = [line for line in out.splitlines() if line]
    assert "noteable" in lines
    assert "noteable_id" in lines
    assert "unparseable" in lines
    assert "recieve" not in lines


def test_main_is_deterministic() -> None:
    rc1, out1 = _run_main(_SAMPLE_LOG)
    rc2, out2 = _run_main(_SAMPLE_LOG)
    assert rc1 == rc2 == 0
    assert out1 == out2
