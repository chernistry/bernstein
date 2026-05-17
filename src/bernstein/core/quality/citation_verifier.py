"""Citation and reference existence verifier (quality gate).

Closes #1402.

This module extracts citation-like spans from agent-produced artefacts
(URLs, DOIs, arXiv IDs, GitHub issue/PR refs, repo-local file paths) and
verifies that every span resolves to a real target. Hallucinated citations
are returned as ``unresolved`` so the gate can block the merge.

Design notes
------------

* Public surface: :func:`verify_citations` plus the :class:`CitationReport`
  dataclass. Both are import-stable.
* Offline mode skips every network call -- only filesystem checks run and
  hosts listed in ``allowed_hosts`` (or the IETF reserved test-hosts) are
  treated as resolvable. This keeps the gate usable in air-gapped CI.
* Hot-path: when the gate is not opted into via
  ``bernstein.yaml :: quality.verify_citations: true`` the module is not
  imported by :mod:`bernstein.core.quality.gate_pipeline` and costs zero.
* No third-party HTTP libraries. The verifier uses :mod:`urllib.request`
  with a 3 second timeout and HEAD probes (falling back to GET on 405).
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Span extraction regexes
# ---------------------------------------------------------------------------

# arXiv IDs use either the legacy "category/YYMMNNN" or the modern
# "YYMM.NNNNN" form. We accept both. The optional "v<digit>" suffix is the
# version pin.
_ARXIV_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?ix)
    arxiv:\s*
    (
        \d{4}\.\d{4,5}(?:v\d+)?           # modern
        |
        [a-z\-]+(?:\.[a-z]{2})?/\d{7}     # legacy
    )
    """,
)

# DOI grammar per Crossref: "10." + registrant + "/" + suffix. We restrict
# the suffix to printable characters that are not whitespace so we do not
# swallow surrounding punctuation, then we trim trailing punctuation that
# routinely follows DOIs in prose.
_DOI_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?ix)
    \b
    (
        10\.\d{4,9}                       # registrant
        /
        [^\s\"'<>]+                      # suffix
    )
    """,
)

# GitHub "org/repo#N" pattern. The slug parts use GitHub's actual rules:
# alphanumerics plus "-", "_", ".".
_GH_REF_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?x)
    (?<![\w/])
    ([A-Za-z0-9][A-Za-z0-9\-_.]*)
    /
    ([A-Za-z0-9][A-Za-z0-9\-_.]*)
    \#
    (\d+)
    \b
    """,
)

# URL pattern matches both http and https schemes. We deliberately keep the
# match greedy enough to capture query strings and fragments but trim a
# trailing set of punctuation characters at the end so URLs embedded in
# prose ("see https://foo.com.") do not absorb the surrounding period.
_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?ix)
    \b(https?://[^\s\"'<>\)]+)
    """,
)

# Repo-local file paths look like "src/.../foo.py" or "tests/.../bar.py".
# We restrict to "<word>/.../*.<ext>" with a known extension to keep the
# false-positive rate low.
_FILE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?x)
    (?<![\w/])
    (
        (?:src|tests|docs|scripts|benchmarks|examples)
        /
        [A-Za-z0-9_./-]+
        \.
        (?:py|md|yaml|yml|toml|json|rst|txt|sh)
    )
    \b
    """,
)

_DOI_TRAILING_PUNCT: Final[str] = ".,;:)]}\"'>"

# Hosts that are always considered resolvable in offline mode. RFC 2606
# reserves example.com / example.org / example.net for documentation use,
# and the verifier treats them as fixtures that should never fail offline.
_OFFLINE_ALLOWED_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "www.example.com",
        "www.example.org",
        "www.example.net",
        "localhost",
        "127.0.0.1",
    },
)

# Network timeout for HEAD/GET probes.
_HTTP_TIMEOUT_S: Final[float] = 3.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """A single citation-like span extracted from an artefact.

    Attributes:
        kind: Citation kind: ``"url"``, ``"doi"``, ``"arxiv"``, ``"github"``,
            or ``"path"``.
        value: The verbatim span text (post-normalization).
        offset: Byte offset of the span in the original artefact text. Used
            for human-readable error reporting.
    """

    kind: str
    value: str
    offset: int


@dataclass(frozen=True)
class CitationReport:
    """Result of running the verifier against a single artefact.

    Attributes:
        total: Total number of citation spans extracted.
        resolved: Spans that resolved to a real target.
        unresolved: Spans that did not resolve (hallucinated, dead-link, or
            malformed). The gate fails when this list is non-empty.
        suspicious: Spans that resolved but looked off (IDN homoglyphs,
            self-signed redirects, etc.). The gate warns but does not fail.
        skipped: Spans that were skipped because they were unreachable in
            offline mode or did not pass an allow-host filter. These are
            never counted as failures.
    """

    total: int
    resolved: tuple[Citation, ...] = field(default_factory=tuple)
    unresolved: tuple[Citation, ...] = field(default_factory=tuple)
    suspicious: tuple[Citation, ...] = field(default_factory=tuple)
    skipped: tuple[Citation, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """Return True when no citation is unresolved."""
        return not self.unresolved

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable rendering of the report."""
        return {
            "total": self.total,
            "ok": self.ok,
            "resolved": [_citation_to_dict(c) for c in self.resolved],
            "unresolved": [_citation_to_dict(c) for c in self.unresolved],
            "suspicious": [_citation_to_dict(c) for c in self.suspicious],
            "skipped": [_citation_to_dict(c) for c in self.skipped],
        }


def _citation_to_dict(c: Citation) -> dict[str, object]:
    return {"kind": c.kind, "value": c.value, "offset": c.offset}


# ---------------------------------------------------------------------------
# Span extraction
# ---------------------------------------------------------------------------


def extract_citations(text: str) -> list[Citation]:
    """Extract every citation-like span from *text*.

    The order of citations in the returned list matches their order of
    appearance in *text*. Overlapping matches (for example a DOI that
    appears inside a URL) are deduplicated: URL matches always win over
    DOI matches because the URL form is unambiguous.

    Args:
        text: Free-form artefact text.

    Returns:
        List of :class:`Citation` records.
    """
    citations: list[Citation] = []
    occupied: list[tuple[int, int]] = []

    def _claim(start: int, end: int) -> bool:
        for o_start, o_end in occupied:
            if start < o_end and end > o_start:
                return False
        occupied.append((start, end))
        return True

    # URLs first so they win over DOI / path matches embedded inside them.
    for match in _URL_RE.finditer(text):
        raw = match.group(1)
        normalized = _strip_trailing_punct(raw)
        end = match.start(1) + len(normalized)
        if _claim(match.start(1), end):
            citations.append(Citation(kind="url", value=normalized, offset=match.start(1)))

    for match in _ARXIV_RE.finditer(text):
        if _claim(match.start(), match.end()):
            citations.append(Citation(kind="arxiv", value=match.group(1), offset=match.start(1)))

    for match in _DOI_RE.finditer(text):
        raw = match.group(1)
        normalized = _strip_trailing_punct(raw)
        end = match.start(1) + len(normalized)
        if _claim(match.start(1), end):
            citations.append(Citation(kind="doi", value=normalized, offset=match.start(1)))

    for match in _GH_REF_RE.finditer(text):
        if _claim(match.start(), match.end()):
            citations.append(Citation(kind="github", value=match.group(0), offset=match.start()))

    for match in _FILE_PATH_RE.finditer(text):
        if _claim(match.start(1), match.end(1)):
            citations.append(Citation(kind="path", value=match.group(1), offset=match.start(1)))

    citations.sort(key=lambda c: c.offset)
    return citations


def _strip_trailing_punct(value: str) -> str:
    """Trim trailing punctuation that prose routinely appends to citations."""
    return value.rstrip(_DOI_TRAILING_PUNCT)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _normalize_host(value: str) -> str:
    """Return the lower-cased host from a URL, or empty string on failure."""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return ""
    host = parsed.hostname or ""
    return host.lower()


def _is_idn_suspicious(host: str) -> bool:
    """Flag hostnames that mix scripts (a classic homoglyph trick)."""
    if not host:
        return False
    # Pure ASCII is always safe.
    if host.isascii():
        return False
    # Mixed ASCII + non-ASCII in the same label is the canonical homoglyph
    # red flag (e.g. "g00gle" with a Cyrillic "o" interleaved).
    for label in host.split("."):
        has_ascii_letter = any(ch.isascii() and ch.isalpha() for ch in label)
        has_non_ascii = any(not ch.isascii() for ch in label)
        if has_ascii_letter and has_non_ascii:
            return True
    return False


def _check_url(
    citation: Citation,
    *,
    offline: bool,
    allowed_hosts: frozenset[str] | None,
) -> tuple[str, bool]:
    """Resolve a URL citation.

    Returns:
        Tuple ``(bucket, suspicious)`` where bucket is one of
        ``"resolved"``, ``"unresolved"``, or ``"skipped"``.
    """
    host = _normalize_host(citation.value)
    if not host:
        return "unresolved", False

    suspicious = _is_idn_suspicious(host)

    if allowed_hosts is not None and host not in allowed_hosts:
        return "skipped", suspicious

    if offline:
        if host in _OFFLINE_ALLOWED_HOSTS or (allowed_hosts is not None and host in allowed_hosts):
            return "resolved", suspicious
        return "skipped", suspicious

    try:
        request = urllib.request.Request(citation.value, method="HEAD")
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as resp:
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            return _check_url_fallback_get(citation), suspicious
        if 200 <= exc.code < 400:
            return "resolved", suspicious
        return "unresolved", suspicious
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "unresolved", suspicious

    if 200 <= status < 400:
        return "resolved", suspicious
    return "unresolved", suspicious


def _check_url_fallback_get(citation: Citation) -> str:
    """Fallback for servers that reject HEAD with 405."""
    try:
        request = urllib.request.Request(citation.value, method="GET")
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as resp:
            return "resolved" if 200 <= int(resp.status) < 400 else "unresolved"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "unresolved"


def _check_arxiv(citation: Citation, *, offline: bool) -> str:
    """Resolve an arXiv ID via the public abstract URL."""
    if offline:
        # Validate shape only: arXiv IDs are deterministic enough that a
        # shape check is meaningful even offline.
        return "resolved" if _ARXIV_RE.fullmatch(f"arXiv:{citation.value}") else "unresolved"
    url = f"https://arxiv.org/abs/{citation.value}"
    probe = Citation(kind="url", value=url, offset=citation.offset)
    bucket, _ = _check_url(probe, offline=False, allowed_hosts=None)
    return bucket


def _check_doi(citation: Citation, *, offline: bool) -> str:
    """Resolve a DOI via dx.doi.org."""
    if offline:
        return "resolved" if _DOI_RE.fullmatch(citation.value) else "unresolved"
    url = f"https://doi.org/{citation.value}"
    probe = Citation(kind="url", value=url, offset=citation.offset)
    bucket, _ = _check_url(probe, offline=False, allowed_hosts=None)
    return bucket


def _check_github(citation: Citation, *, offline: bool) -> str:
    """Resolve a "org/repo#N" GitHub reference."""
    match = _GH_REF_RE.fullmatch(citation.value)
    if match is None:
        return "unresolved"
    if offline:
        return "resolved"
    org, repo, num = match.group(1), match.group(2), match.group(3)
    url = f"https://github.com/{org}/{repo}/issues/{num}"
    probe = Citation(kind="url", value=url, offset=citation.offset)
    bucket, _ = _check_url(probe, offline=False, allowed_hosts=None)
    return bucket


def _check_path(citation: Citation, *, repo_root: Path | None) -> str:
    """Resolve a repo-local file path against *repo_root*."""
    root = repo_root or Path.cwd()
    candidate = (root / citation.value).resolve()
    # Containment check: refuse to confirm paths that escape the repo
    # root, otherwise an artefact could "cite" /etc/passwd.
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return "unresolved"
    return "resolved" if candidate.exists() else "unresolved"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_citations(
    text: str,
    *,
    offline: bool = False,
    allowed_hosts: list[str] | None = None,
    repo_root: Path | None = None,
) -> CitationReport:
    """Verify every citation in *text* against an authoritative source.

    Args:
        text: Free-form artefact text. Pass the raw markdown / plaintext
            body; the verifier handles its own extraction.
        offline: When True, network probes are skipped. URLs whose host
            sits in :data:`_OFFLINE_ALLOWED_HOSTS` (RFC 2606 reserved
            test hosts) or in ``allowed_hosts`` resolve as a fixture.
        allowed_hosts: Optional allow-list of hostnames. When set, every
            URL whose host is not in the list is skipped (never failed).
            Useful for whitelisting canonical sources in a slow CI env.
        repo_root: Filesystem root for ``path`` citations. Defaults to
            the current working directory.

    Returns:
        :class:`CitationReport`.
    """
    citations = extract_citations(text)
    allow_set = frozenset(h.lower() for h in allowed_hosts) if allowed_hosts is not None else None

    resolved: list[Citation] = []
    unresolved: list[Citation] = []
    suspicious: list[Citation] = []
    skipped: list[Citation] = []

    for citation in citations:
        bucket, is_suspicious = _dispatch(
            citation,
            offline=offline,
            allowed_hosts=allow_set,
            repo_root=repo_root,
        )
        if bucket == "resolved":
            resolved.append(citation)
        elif bucket == "unresolved":
            unresolved.append(citation)
        elif bucket == "skipped":
            skipped.append(citation)
        if is_suspicious:
            suspicious.append(citation)

    return CitationReport(
        total=len(citations),
        resolved=tuple(resolved),
        unresolved=tuple(unresolved),
        suspicious=tuple(suspicious),
        skipped=tuple(skipped),
    )


def _dispatch(
    citation: Citation,
    *,
    offline: bool,
    allowed_hosts: frozenset[str] | None,
    repo_root: Path | None,
) -> tuple[str, bool]:
    """Route *citation* to the right resolver, return (bucket, suspicious)."""
    if citation.kind == "url":
        return _check_url(citation, offline=offline, allowed_hosts=allowed_hosts)
    if citation.kind == "arxiv":
        return _check_arxiv(citation, offline=offline), False
    if citation.kind == "doi":
        return _check_doi(citation, offline=offline), False
    if citation.kind == "github":
        return _check_github(citation, offline=offline), False
    if citation.kind == "path":
        return _check_path(citation, repo_root=repo_root), False
    return "unresolved", False


# ---------------------------------------------------------------------------
# Gate-pipeline adapter
# ---------------------------------------------------------------------------


def gate_verify_citations(
    artefact_text: str,
    *,
    offline: bool = False,
    allowed_hosts: list[str] | None = None,
    repo_root: Path | None = None,
) -> tuple[bool, str]:
    """Adapter used by the quality gate pipeline.

    Returns:
        Tuple ``(passed, details)`` where ``passed`` is True iff no
        citation went unresolved, and ``details`` is a one-line summary
        suitable for inclusion in a :class:`GateResult`.
    """
    report = verify_citations(
        artefact_text,
        offline=offline,
        allowed_hosts=allowed_hosts,
        repo_root=repo_root,
    )
    if report.ok:
        return True, (
            f"citation_verifier: {len(report.resolved)}/{report.total} resolved"
            f" ({len(report.skipped)} skipped, {len(report.suspicious)} suspicious)"
        )
    unresolved_summary = ", ".join(f"{c.kind}:{c.value}" for c in report.unresolved[:5])
    if len(report.unresolved) > 5:
        unresolved_summary += f" (+{len(report.unresolved) - 5} more)"
    return False, f"citation_verifier: {len(report.unresolved)} unresolved -- {unresolved_summary}"
