"""Unit tests for :mod:`bernstein.core.quality.citation_verifier` (issue #1402).

These tests run entirely offline. Network-bound resolution paths are tested
in ``tests/integration/quality/test_citation_verifier_http.py`` against a
fixture HTTP server.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bernstein.core.quality.citation_verifier import (
    Citation,
    CitationReport,
    _is_idn_suspicious,
    _normalize_host,
    _strip_trailing_punct,
    extract_citations,
    gate_verify_citations,
    verify_citations,
)

# ---------------------------------------------------------------------------
# extract_citations -- URL extraction
# ---------------------------------------------------------------------------


def test_extract_simple_http_url() -> None:
    cs = extract_citations("see http://example.com for details")
    assert any(c.kind == "url" and c.value == "http://example.com" for c in cs)


def test_extract_simple_https_url() -> None:
    cs = extract_citations("see https://example.com for details")
    assert any(c.kind == "url" and c.value == "https://example.com" for c in cs)


def test_extract_url_strips_trailing_period() -> None:
    cs = extract_citations("Visit https://example.com.")
    urls = [c for c in cs if c.kind == "url"]
    assert urls == [Citation(kind="url", value="https://example.com", offset=urls[0].offset)]


def test_extract_url_strips_trailing_comma() -> None:
    cs = extract_citations("Visit https://example.com, then continue")
    urls = [c for c in cs if c.kind == "url"]
    assert urls[0].value == "https://example.com"


def test_extract_url_preserves_path_query_fragment() -> None:
    cs = extract_citations("see https://example.com/path?x=1#frag end")
    urls = [c for c in cs if c.kind == "url"]
    assert urls[0].value == "https://example.com/path?x=1#frag"


def test_extract_multiple_urls() -> None:
    cs = extract_citations("a https://example.com b https://example.org c")
    urls = [c.value for c in cs if c.kind == "url"]
    assert "https://example.com" in urls
    assert "https://example.org" in urls


def test_extract_no_url_for_bare_text() -> None:
    cs = extract_citations("plain text without any references")
    assert cs == []


# ---------------------------------------------------------------------------
# extract_citations -- arXiv
# ---------------------------------------------------------------------------


def test_extract_modern_arxiv() -> None:
    cs = extract_citations("see arXiv:2401.12345 for details")
    assert any(c.kind == "arxiv" and c.value == "2401.12345" for c in cs)


def test_extract_modern_arxiv_with_version() -> None:
    cs = extract_citations("arXiv:2401.12345v2")
    arx = [c for c in cs if c.kind == "arxiv"]
    assert arx and arx[0].value == "2401.12345v2"


def test_extract_legacy_arxiv() -> None:
    cs = extract_citations("see arXiv:cs.LG/0701001 for details")
    arx = [c for c in cs if c.kind == "arxiv"]
    assert arx and arx[0].value == "cs.LG/0701001"


def test_extract_arxiv_case_insensitive() -> None:
    cs = extract_citations("ARXIV:2401.12345")
    assert any(c.kind == "arxiv" for c in cs)


def test_extract_arxiv_missing_id_not_matched() -> None:
    cs = extract_citations("arXiv: not an id")
    assert not any(c.kind == "arxiv" for c in cs)


# ---------------------------------------------------------------------------
# extract_citations -- DOI
# ---------------------------------------------------------------------------


def test_extract_doi() -> None:
    cs = extract_citations("see 10.1038/nature12373 for details")
    assert any(c.kind == "doi" and c.value == "10.1038/nature12373" for c in cs)


def test_extract_doi_strips_trailing_period() -> None:
    cs = extract_citations("see 10.1038/nature12373.")
    dois = [c for c in cs if c.kind == "doi"]
    assert dois and dois[0].value == "10.1038/nature12373"


def test_extract_doi_complex_suffix() -> None:
    cs = extract_citations("doi 10.1234/abc.def-ghi_jkl(2023)")
    dois = [c for c in cs if c.kind == "doi"]
    assert dois and dois[0].value.startswith("10.1234/abc.def-ghi_jkl")


def test_extract_no_doi_for_short_registrant() -> None:
    cs = extract_citations("number 10.1/x is too short")
    assert not any(c.kind == "doi" for c in cs)


# ---------------------------------------------------------------------------
# extract_citations -- GitHub refs
# ---------------------------------------------------------------------------


def test_extract_github_issue_ref() -> None:
    cs = extract_citations("see chernistry/bernstein#1402 for context")
    gh = [c for c in cs if c.kind == "github"]
    assert gh and gh[0].value == "chernistry/bernstein#1402"


def test_extract_github_repo_with_dot() -> None:
    cs = extract_citations("see foo/bar.baz#42")
    gh = [c for c in cs if c.kind == "github"]
    assert gh and gh[0].value == "foo/bar.baz#42"


def test_extract_no_github_without_hash() -> None:
    cs = extract_citations("just org/repo here")
    assert not any(c.kind == "github" for c in cs)


# ---------------------------------------------------------------------------
# extract_citations -- file paths
# ---------------------------------------------------------------------------


def test_extract_repo_file_path_py() -> None:
    cs = extract_citations("changed src/bernstein/core/quality/citation_verifier.py here")
    paths = [c for c in cs if c.kind == "path"]
    assert paths and paths[0].value == "src/bernstein/core/quality/citation_verifier.py"


def test_extract_repo_file_path_md() -> None:
    cs = extract_citations("see docs/architecture/DESIGN.md")
    paths = [c for c in cs if c.kind == "path"]
    assert paths and paths[0].value == "docs/architecture/DESIGN.md"


def test_extract_no_path_for_unknown_root() -> None:
    cs = extract_citations("/etc/passwd is bad")
    assert not any(c.kind == "path" for c in cs)


def test_extract_no_path_for_unknown_extension() -> None:
    cs = extract_citations("src/foo/bar.xyz unknown ext")
    assert not any(c.kind == "path" for c in cs)


# ---------------------------------------------------------------------------
# extract_citations -- ordering and de-dup
# ---------------------------------------------------------------------------


def test_extract_returns_in_document_order() -> None:
    text = "first arXiv:2401.12345 then https://example.com then 10.1234/foo.bar"
    cs = extract_citations(text)
    offsets = [c.offset for c in cs]
    assert offsets == sorted(offsets)


def test_extract_does_not_double_match_url_and_doi() -> None:
    # A DOI embedded inside a URL must not also be reported as a DOI.
    cs = extract_citations("see https://doi.org/10.1038/nature12373 for refs")
    kinds = [c.kind for c in cs]
    assert kinds.count("doi") == 0
    assert kinds.count("url") == 1


# ---------------------------------------------------------------------------
# verify_citations -- offline mode
# ---------------------------------------------------------------------------


def test_offline_example_com_resolves() -> None:
    report = verify_citations("see https://example.com here", offline=True)
    assert report.ok
    assert report.total == 1
    assert len(report.resolved) == 1


def test_offline_unknown_host_is_skipped_not_failed() -> None:
    report = verify_citations("see https://unknown-host-xyz.invalid here", offline=True)
    assert report.ok  # not failed
    assert len(report.skipped) == 1
    assert len(report.unresolved) == 0


def test_offline_allowed_hosts_resolves() -> None:
    report = verify_citations(
        "see https://mycorp.example here",
        offline=True,
        allowed_hosts=["mycorp.example"],
    )
    assert report.ok
    assert len(report.resolved) == 1


def test_offline_doi_shape_check_resolves() -> None:
    report = verify_citations("doi 10.1038/nature12373", offline=True)
    assert report.ok
    assert len(report.resolved) == 1


def test_offline_arxiv_shape_check_resolves() -> None:
    report = verify_citations("arXiv:2401.12345", offline=True)
    assert report.ok
    assert len(report.resolved) == 1


def test_offline_github_ref_resolves() -> None:
    report = verify_citations("see foo/bar#123", offline=True)
    assert report.ok
    assert len(report.resolved) == 1


# ---------------------------------------------------------------------------
# verify_citations -- allow-host filter
# ---------------------------------------------------------------------------


def test_allowed_hosts_filter_skips_non_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    # When allowed_hosts is set, a non-listed URL must be skipped (not
    # failed) even when offline mode is off.
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("network must not be called when filtered")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    report = verify_citations(
        "see https://blocked.example",
        allowed_hosts=["allowed.example"],
    )
    assert report.ok
    assert len(report.skipped) == 1


def test_allowed_hosts_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("network must not be called when offline")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    report = verify_citations(
        "see https://MyCorp.Example",
        offline=True,
        allowed_hosts=["mycorp.example"],
    )
    assert len(report.resolved) == 1


# ---------------------------------------------------------------------------
# verify_citations -- malformed URLs
# ---------------------------------------------------------------------------


def test_malformed_url_no_host_is_unresolved() -> None:
    # urlsplit gives an empty hostname here -> unresolved.
    cs = [Citation(kind="url", value="http://", offset=0)]
    with patch("bernstein.core.quality.citation_verifier.extract_citations", return_value=cs):
        report = verify_citations("dummy")
    assert not report.ok
    assert len(report.unresolved) == 1


def test_truncated_url_is_unresolved() -> None:
    cs = [Citation(kind="url", value="ht!tp://broken", offset=0)]
    with patch("bernstein.core.quality.citation_verifier.extract_citations", return_value=cs):
        report = verify_citations("dummy")
    assert not report.ok


# ---------------------------------------------------------------------------
# verify_citations -- repo-local paths
# ---------------------------------------------------------------------------


def test_path_resolves_when_file_exists(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("# stub")
    report = verify_citations("changed src/foo.py", repo_root=tmp_path)
    assert report.ok
    assert len(report.resolved) == 1


def test_path_unresolved_when_file_missing(tmp_path: Path) -> None:
    report = verify_citations("changed src/missing.py", repo_root=tmp_path)
    assert not report.ok
    assert len(report.unresolved) == 1


def test_path_escape_attempt_is_unresolved(tmp_path: Path) -> None:
    # A traversal that escapes the repo root must not be reported as
    # resolved even if the absolute target exists.
    report = verify_citations("see src/../../etc/passwd-no", repo_root=tmp_path)
    # Either unresolved or not extracted; either way the gate must not
    # call it resolved.
    assert all(c.kind != "path" or c not in report.resolved for c in report.resolved)


# ---------------------------------------------------------------------------
# Dead link detection (offline allow-host whitelist disabled)
# ---------------------------------------------------------------------------


def test_dead_url_unresolved_via_urlerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error as _urlerr

    def _raise(*args: object, **kwargs: object) -> None:
        raise _urlerr.URLError("no route")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    report = verify_citations("see https://dead.example.test")
    assert not report.ok
    assert len(report.unresolved) == 1


def test_dead_url_unresolved_via_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise TimeoutError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    report = verify_citations("see https://slow.example.test")
    assert not report.ok


def test_404_returns_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error as _urlerr

    def _raise(*args: object, **kwargs: object) -> None:
        raise _urlerr.HTTPError("https://x", 404, "not found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    report = verify_citations("see https://gone.example.test")
    assert not report.ok


def test_405_falls_back_to_get(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error as _urlerr

    calls: list[str] = []

    class _FakeResp:
        status = 200

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _maybe_raise(req: object, *args: object, **kwargs: object) -> _FakeResp:
        method = getattr(req, "get_method", lambda: "")()
        calls.append(method)
        if method == "HEAD":
            raise _urlerr.HTTPError("https://x", 405, "method not allowed", {}, None)  # type: ignore[arg-type]
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _maybe_raise)
    report = verify_citations("see https://no-head.example.test")
    assert report.ok
    assert "HEAD" in calls
    assert "GET" in calls


def test_redirect_3xx_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResp:
        status = 301

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())
    report = verify_citations("see https://redirect.example.test")
    assert report.ok


# ---------------------------------------------------------------------------
# IDN handling
# ---------------------------------------------------------------------------


def test_pure_ascii_host_not_suspicious() -> None:
    assert not _is_idn_suspicious("example.com")


def test_pure_unicode_host_not_suspicious() -> None:
    # Pure-script labels are common (intl TLDs) and must not be flagged.
    assert not _is_idn_suspicious("пример.рф")


def test_mixed_script_label_flagged() -> None:
    # "exаmple.com" -- the "а" is Cyrillic. Pyright/Ruff allow unicode in
    # source strings; the verifier must flag this label.
    assert _is_idn_suspicious("exаmple.com")  # noqa: RUF001 - intentional homoglyph


def test_idn_suspicious_url_appears_in_report(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResp:
        status = 200

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _FakeResp())
    report = verify_citations("see https://exаmple.com")  # noqa: RUF001 - intentional homoglyph
    assert any(_is_idn_suspicious(_normalize_host(c.value)) for c in report.suspicious)


def test_empty_host_returns_empty_string() -> None:
    assert _normalize_host("not a url at all") == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_strip_trailing_punct_handles_multiple() -> None:
    assert _strip_trailing_punct("https://x.com).") == "https://x.com"


def test_strip_trailing_punct_keeps_significant_chars() -> None:
    assert _strip_trailing_punct("https://x.com/path?a=b") == "https://x.com/path?a=b"


# ---------------------------------------------------------------------------
# CitationReport
# ---------------------------------------------------------------------------


def test_citation_report_ok_true_when_no_unresolved() -> None:
    report = CitationReport(total=0)
    assert report.ok


def test_citation_report_ok_false_when_unresolved() -> None:
    bad = Citation(kind="url", value="http://x", offset=0)
    report = CitationReport(total=1, unresolved=(bad,))
    assert not report.ok


def test_citation_report_to_dict_is_serialisable() -> None:
    import json as _json

    good = Citation(kind="url", value="https://example.com", offset=4)
    report = CitationReport(total=1, resolved=(good,))
    payload = report.to_dict()
    assert _json.dumps(payload, sort_keys=True)  # must not raise


def test_citation_report_to_dict_includes_all_buckets() -> None:
    citation = Citation(kind="url", value="https://x.com", offset=0)
    report = CitationReport(
        total=4,
        resolved=(citation,),
        unresolved=(citation,),
        suspicious=(citation,),
        skipped=(citation,),
    )
    payload = report.to_dict()
    assert {"total", "ok", "resolved", "unresolved", "suspicious", "skipped"} <= set(payload)


# ---------------------------------------------------------------------------
# Gate adapter
# ---------------------------------------------------------------------------


def test_gate_passes_when_all_resolve() -> None:
    passed, details = gate_verify_citations("see https://example.com", offline=True)
    assert passed
    assert "1/1 resolved" in details


def test_gate_fails_when_unresolved(tmp_path: Path) -> None:
    passed, details = gate_verify_citations(
        "changed src/missing.py", offline=True, repo_root=tmp_path,
    )
    assert not passed
    assert "unresolved" in details
    assert "src/missing.py" in details


def test_gate_summary_truncates_long_unresolved_list(tmp_path: Path) -> None:
    text = " ".join(f"src/missing_{i}.py" for i in range(10))
    passed, details = gate_verify_citations(text, offline=True, repo_root=tmp_path)
    assert not passed
    assert "more" in details


def test_gate_includes_skipped_in_summary() -> None:
    passed, details = gate_verify_citations(
        "see https://not-allowlisted.invalid",
        offline=True,
    )
    assert passed
    assert "skipped" in details


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_text_returns_empty_report() -> None:
    report = verify_citations("", offline=True)
    assert report.total == 0
    assert report.ok


def test_only_whitespace_returns_empty_report() -> None:
    report = verify_citations("   \n\t\n   ", offline=True)
    assert report.total == 0


def test_unicode_text_does_not_crash() -> None:
    report = verify_citations("ссылка на https://example.com тут", offline=True)
    assert report.ok
    assert len(report.resolved) == 1


def test_localhost_resolved_offline() -> None:
    report = verify_citations("see http://localhost:8080", offline=True)
    assert report.ok
    assert len(report.resolved) == 1


def test_very_long_text_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    # The verifier must handle large artefacts without quadratic blowup.
    text = ("filler " * 10_000) + "see https://example.com end"
    report = verify_citations(text, offline=True)
    assert report.ok


def test_multiple_kinds_in_same_text(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").touch()
    text = (
        "fix in src/foo.py per arXiv:2401.12345 (10.1038/nature12373) "
        "see https://example.com chernistry/bernstein#1402"
    )
    report = verify_citations(text, offline=True, repo_root=tmp_path)
    assert report.ok
    assert report.total == 5
    kinds = {c.kind for c in report.resolved}
    assert kinds == {"url", "doi", "arxiv", "github", "path"}


def test_repeat_url_extracted_each_time() -> None:
    cs = extract_citations("a https://example.com b https://example.com c")
    urls = [c for c in cs if c.kind == "url"]
    assert len(urls) == 2
