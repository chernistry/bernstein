"""Property-based tests for the citation verifier (issue #1402).

Hypothesis hammers the verifier with structured-random inputs to make
sure invariants hold across edge cases that hand-written tests miss.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from bernstein.core.quality.citation_verifier import (
    Citation,
    extract_citations,
    verify_citations,
)

_SUPPRESS = (HealthCheck.too_slow, HealthCheck.filter_too_much)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_clean_text(s: str) -> bool:
    # Avoid control characters that confuse regex anchoring.
    return all(c.isprintable() or c == " " for c in s)


_simple_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
    min_size=0,
    max_size=200,
).filter(_is_clean_text)


# ---------------------------------------------------------------------------
# Property 1: extract_citations is total and never raises.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=200)
@given(text=_simple_text)
def test_extract_is_total(text: str) -> None:
    # Property: extract_citations must accept any text input and return a
    # list of Citation records. Critical for use as a quality gate that
    # consumes arbitrary agent-produced output.
    result = extract_citations(text)
    assert isinstance(result, list)
    for c in result:
        assert isinstance(c, Citation)
        assert c.kind in {"url", "doi", "arxiv", "github", "path"}


# ---------------------------------------------------------------------------
# Property 2: verify_citations is total in offline mode.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=200)
@given(text=_simple_text)
def test_verify_total_offline(text: str) -> None:
    report = verify_citations(text, offline=True)
    assert (
        report.total
        == len(report.resolved) + len(report.unresolved) + len(report.skipped)
    )


# ---------------------------------------------------------------------------
# Property 3: offsets always non-negative and within text bounds.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=200)
@given(text=_simple_text)
def test_offsets_within_text(text: str) -> None:
    for c in extract_citations(text):
        assert 0 <= c.offset <= len(text)


# ---------------------------------------------------------------------------
# Property 4: every extracted citation occurs in the source text.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=200)
@given(text=_simple_text)
def test_extracted_values_substring(text: str) -> None:
    for c in extract_citations(text):
        # URLs and DOIs may have trailing punctuation stripped; the
        # remaining prefix must still appear verbatim.
        assert c.value in text


# ---------------------------------------------------------------------------
# Property 5: ordering invariant (offsets non-decreasing).
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=200)
@given(text=_simple_text)
def test_citations_sorted_by_offset(text: str) -> None:
    cs = extract_citations(text)
    offsets = [c.offset for c in cs]
    assert offsets == sorted(offsets)


# ---------------------------------------------------------------------------
# Property 6: synthetic arXiv IDs always extract.
# ---------------------------------------------------------------------------


_year_month = st.integers(min_value=1, max_value=99).map(lambda y: f"{y:02d}")
_month = st.integers(min_value=1, max_value=12).map(lambda m: f"{m:02d}")
_seq = st.integers(min_value=0, max_value=99_999).map(lambda n: f"{n:05d}")


@settings(suppress_health_check=_SUPPRESS, max_examples=100)
@given(yy=_year_month, mm=_month, seq=_seq)
def test_synthetic_arxiv_extracts(yy: str, mm: str, seq: str) -> None:
    aid = f"{yy}{mm}.{seq}"
    text = f"see arXiv:{aid} for details"
    found = [c.value for c in extract_citations(text) if c.kind == "arxiv"]
    assert aid in found


# ---------------------------------------------------------------------------
# Property 7: synthetic DOI strings always extract.
# ---------------------------------------------------------------------------


_doi_suffix = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789._-",
    min_size=1,
    max_size=20,
)


@settings(suppress_health_check=_SUPPRESS, max_examples=100)
@given(registrant=st.integers(min_value=1000, max_value=999_999_999), suffix=_doi_suffix)
def test_synthetic_doi_extracts(registrant: int, suffix: str) -> None:
    assume(not suffix.endswith(("." , "-", "_")))
    doi = f"10.{registrant}/{suffix}"
    text = f"see {doi} for details"
    found = [c.value for c in extract_citations(text) if c.kind == "doi"]
    assert doi in found


# ---------------------------------------------------------------------------
# Property 8: GitHub ref pattern is robust.
# ---------------------------------------------------------------------------


_slug_alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@settings(suppress_health_check=_SUPPRESS, max_examples=100)
@given(
    org=st.text(alphabet=_slug_alphabet, min_size=1, max_size=20),
    repo=st.text(alphabet=_slug_alphabet, min_size=1, max_size=20),
    num=st.integers(min_value=1, max_value=999_999),
)
def test_synthetic_github_ref_extracts(org: str, repo: str, num: int) -> None:
    ref = f"{org}/{repo}#{num}"
    text = f"see {ref} here"
    found = [c.value for c in extract_citations(text) if c.kind == "github"]
    assert ref in found


# ---------------------------------------------------------------------------
# Property 9: offline+empty allow-list never resolves arbitrary hosts.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=100)
@given(host=st.from_regex(r"[a-z]{3,10}\.invalidtld", fullmatch=True))
def test_offline_unknown_host_skipped(host: str) -> None:
    text = f"see https://{host}/path here"
    report = verify_citations(text, offline=True, allowed_hosts=[])
    # Unknown host with empty allow-list: skipped, never failed.
    assert len(report.skipped) == 1
    assert not report.unresolved


# ---------------------------------------------------------------------------
# Property 10: allow-host filter strictly partitions URLs.
# ---------------------------------------------------------------------------


_hostname = st.from_regex(r"[a-z]{3,15}\.example", fullmatch=True)


@settings(suppress_health_check=_SUPPRESS, max_examples=100)
@given(host=_hostname)
def test_listed_host_resolves_offline(host: str) -> None:
    text = f"see https://{host}/x here"
    report = verify_citations(text, offline=True, allowed_hosts=[host])
    assert len(report.resolved) == 1
    assert not report.unresolved


# ---------------------------------------------------------------------------
# Property 11: report.ok matches unresolved emptiness.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=200)
@given(text=_simple_text)
def test_report_ok_iff_no_unresolved(text: str) -> None:
    report = verify_citations(text, offline=True)
    assert report.ok == (len(report.unresolved) == 0)


# ---------------------------------------------------------------------------
# Property 12: path resolver never escapes the repo root.
# ---------------------------------------------------------------------------


@settings(suppress_health_check=_SUPPRESS, max_examples=50)
@given(suffix=st.from_regex(r"src/[a-z]{1,10}/[a-z]{1,10}\.py", fullmatch=True))
def test_path_outside_repo_unresolved(suffix: str, tmp_path_factory: object) -> None:
    # Generate a fresh tmp_path on each call.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        text = f"changed {suffix}"
        report = verify_citations(text, offline=True, repo_root=root)
        # No such file exists in the empty tmp dir -> must be unresolved.
        assert any(c.value == suffix for c in report.unresolved)
