"""Static supply-chain scanner for third-party MCP server source bundles.

Pattern-matching scanner over an MCP server's source. Targets the four
attack classes that drove the OpenClaw 433+ CVE corpus by May 6 2026:

1. **Path traversal** - tool handlers that join user-supplied path
   components without ``Path.resolve`` + a strict prefix check
   (Anthropic Git MCP CVE-2025-68145 lineage).
2. **Shell injection** - ``subprocess`` calls with ``shell=True`` or with
   user-supplied args concatenated into the command without
   ``shlex.quote`` (CVE-2026-25253 OpenClaw gateway URL auth-token RCE
   lineage).
3. **OAuth callback RCE** - callback handlers without a redirect-URI
   allow-list check (CVE-2025-6514 ``mcp-remote`` RCE that compromised
   ~437K developer environments).
4. **Scope escalation** - flows that fetch a token then re-use the same
   token with a broadened ``scope=`` parameter (CVE-2026-32922 OpenClaw
   scope-escalation, CVSS 9.9).

Plus a **known-bad-package** gate: package names on a denylist (compromised
or typosquat publishers) are flagged immediately. This is not a substitute
for full SBOM diffing - when the manifest declares a content hash we also
prefer to compare against a locked dependency hash (the hook is exposed on
:func:`scan_dependency_diff` for the manager to call when a lockfile is
present).

The scanner uses regex/string matching deliberately: an AST visitor would
be more precise but only works against Python sources, while MCP servers
ship in Python, Node, Go, and Rust. The ticket's "smallest viable slice"
calls for breadth, not depth - this layer flags the obvious patterns and
defers AST-level taint tracking to a future PR.

Example::

    from bernstein.core.protocols.mcp.mcp_scanner import scan_mcp_bundle

    findings = scan_mcp_bundle(
        bundle_files={"tools/exec.py": Path("...").read_text()},
        package_name="example-mcp",
    )
    for f in findings:
        print(f.severity, f.rule, f.path, f.line, f.message)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_KNOWN_BAD_PACKAGES",
    "ScannerFinding",
    "ScannerSeverity",
    "scan_dependency_diff",
    "scan_mcp_bundle",
]


# ---------------------------------------------------------------------------
# Constants / fixed denylist (seeded from public OpenClaw / mcp-remote
# advisories). Operators extend via the ``known_bad_packages`` parameter.
# ---------------------------------------------------------------------------

#: Public CVE-tracked compromised MCP package names. The list is small
#: and conservative; the scanner is intended to *complement* an external
#: vulnerability feed, not replace it.
DEFAULT_KNOWN_BAD_PACKAGES: frozenset[str] = frozenset(
    {
        # CVE-2025-6514 - mcp-remote RCE, 437K env compromised
        "mcp-remote",
        # placeholders for the publicly tracked OpenClaw 2026-25253/32922
        # gateway packages; operators are expected to supplement with their
        # own intel feed via the function arg.
        "openclaw-gateway-vulnerable",
    }
)


class ScannerSeverity:
    """String-typed severities (kept as strings so log/JSON paths are flat)."""

    INFO: str = "info"
    LOW: str = "low"
    MEDIUM: str = "medium"
    HIGH: str = "high"
    CRITICAL: str = "critical"


@dataclass(frozen=True)
class ScannerFinding:
    """A single static-analysis finding from the MCP supply-chain scanner.

    Attributes:
        rule: Stable rule identifier (e.g. ``"path_traversal"``).
        severity: One of :class:`ScannerSeverity` codes.
        path: Source path (relative to the bundle root) where the finding
            triggered, or empty for bundle-level findings.
        line: 1-indexed source line, or 0 for bundle-level findings.
        message: Human-readable description.
        cwe: CWE identifier when one applies, e.g. ``"CWE-78"``.
        remediation: Short remediation hint.
    """

    rule: str
    severity: str
    path: str
    line: int
    message: str
    cwe: str = ""
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible summary dict."""
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "cwe": self.cwe,
            "remediation": self.remediation,
        }


# ---------------------------------------------------------------------------
# Pattern catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    """Internal rule definition; one per attack class.

    Attributes:
        rule_id: Stable rule identifier echoed onto the finding.
        severity: One of :class:`ScannerSeverity`.
        cwe: CWE code for the finding (rendered into reports).
        pattern: Regex matched against each non-empty, non-comment source
            line.
        message: Human-readable finding message.
        remediation: Short remediation hint.
        safe_pattern: When set and matched **on the same line**, the
            finding is suppressed. Used for cheap inline disqualifiers
            like ``shlex.quote(...)`` or ``.resolve()``.
        file_safe_pattern: When set and matched **anywhere in the file**,
            the finding is suppressed for the whole file. Used for
            multi-line allow-list checks like ``ALLOWED_REDIRECTS`` that
            commonly live one or two lines below the vulnerable site.
    """

    rule_id: str
    severity: str
    cwe: str
    pattern: re.Pattern[str]
    message: str
    remediation: str
    safe_pattern: re.Pattern[str] | None = None
    file_safe_pattern: re.Pattern[str] | None = None

    languages: frozenset[str] = field(default_factory=frozenset)


# Single source of truth for the four OpenClaw attack classes.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        rule_id="path_traversal",
        severity=ScannerSeverity.HIGH,
        cwe="CWE-22",
        # Common path-join with a user-supplied component, without resolve()
        # or a strict prefix check on the same line. Catches both Python
        # (os.path.join, Path / "...", open(<var>)) and Node (path.join).
        pattern=re.compile(
            r"(os\.path\.join|path\.join|Path\([^)]*\)\s*/|open\s*\()",
            re.IGNORECASE,
        ),
        safe_pattern=re.compile(r"(\.resolve\(\)|startswith\(|is_relative_to\()"),
        message=(
            "path join uses an external component without resolve() + prefix "
            "check (CVE-2025-68145 Anthropic Git MCP path-traversal lineage)"
        ),
        remediation=(
            "call Path(...).resolve() and assert the resolved path is_relative_to(allowed_root) before opening"
        ),
    ),
    _Rule(
        rule_id="shell_injection",
        severity=ScannerSeverity.CRITICAL,
        cwe="CWE-78",
        # Python subprocess(..., shell=True) and Node child_process.exec
        pattern=re.compile(
            r"(subprocess\.(?:Popen|run|call|check_output|check_call)\s*\([^)]*shell\s*=\s*True"
            r"|child_process\.exec\s*\(|\.exec_command\s*\()",
            re.IGNORECASE,
        ),
        safe_pattern=re.compile(r"shlex\.quote\("),
        message=("shell-mode subprocess / exec with non-quoted arg (CVE-2026-25253 OpenClaw gateway RCE lineage)"),
        remediation=(
            "drop shell=True; pass a list of args, or wrap user input "
            "with shlex.quote() (Python) / use spawn() with array (Node)"
        ),
    ),
    _Rule(
        rule_id="oauth_callback_rce",
        severity=ScannerSeverity.CRITICAL,
        cwe="CWE-601",
        # OAuth /callback handlers using the redirect-URI from the request
        # without a fixed allow-list. Heuristic: the literal "redirect_uri"
        # next to a request-derived value with no obvious allow-list check.
        pattern=re.compile(
            r"redirect_uri\s*[=:]",
            re.IGNORECASE,
        ),
        # Per-line safe pattern (e.g. inline allowlist check)
        safe_pattern=re.compile(r"(ALLOWED_REDIRECTS|redirect_allowlist|in\s+ALLOWED_)"),
        # File-level safe pattern: the allow-list is commonly defined a
        # few lines below the assignment, so a per-line check would
        # flag the assignment site even when the file is in fact safe.
        file_safe_pattern=re.compile(
            r"(ALLOWED_REDIRECTS|redirect_allowlist|REDIRECT_URI_ALLOWLIST"
            r"|allowed_redirect_uris)"
        ),
        message=(
            "OAuth callback uses redirect_uri without an allow-list check "
            "(CVE-2025-6514 mcp-remote RCE lineage; ~437K envs compromised)"
        ),
        remediation=(
            "validate redirect_uri against a fixed allowlist before "
            "completing the OAuth dance; never echo the request value back"
        ),
    ),
    _Rule(
        rule_id="scope_escalation",
        severity=ScannerSeverity.HIGH,
        cwe="CWE-269",
        # Reusing a token then setting an expanded scope in the next request.
        pattern=re.compile(
            r"scope\s*=\s*['\"][^'\"]*\b(admin|write|all|\*)\b",
            re.IGNORECASE,
        ),
        message=(
            "OAuth flow expands token scope after issuance "
            "(CVE-2026-32922 OpenClaw scope-escalation, CVSS 9.9, lineage)"
        ),
        remediation=("request the final scope at initial authorization; never broaden scope on token refresh"),
    ),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_mcp_bundle(
    *,
    bundle_files: dict[str, str],
    package_name: str = "",
    known_bad_packages: frozenset[str] | None = None,
) -> list[ScannerFinding]:
    """Scan an MCP server source bundle for the four OpenClaw attack classes.

    Args:
        bundle_files: Mapping of relative path -> source text. Binary files
            should be omitted by the caller.
        package_name: Distribution name (PyPI / npm). When non-empty and
            on the bad-package denylist, a CRITICAL finding is emitted.
        known_bad_packages: Optional override of the default denylist. When
            ``None``, :data:`DEFAULT_KNOWN_BAD_PACKAGES` is used. Pass a
            frozenset extending the default to keep the OpenClaw seeds.

    Returns:
        Sorted list of :class:`ScannerFinding`. Stable ordering keeps
        verifier verdicts reproducible across runs.
    """
    findings: list[ScannerFinding] = []
    bad_pkgs = known_bad_packages if known_bad_packages is not None else DEFAULT_KNOWN_BAD_PACKAGES

    if package_name and package_name in bad_pkgs:
        findings.append(
            ScannerFinding(
                rule="known_bad_package",
                severity=ScannerSeverity.CRITICAL,
                path="",
                line=0,
                message=(
                    f"package {package_name!r} is on the known-bad denylist "
                    "(public CVE / typosquat / compromised publisher)"
                ),
                cwe="CWE-1357",
                remediation=("do not install; if the name is a typo, install the intended publisher's package instead"),
            )
        )

    for path, source in bundle_files.items():
        if not source:
            continue
        findings.extend(_scan_source(path=path, source=source))

    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    return findings


def scan_dependency_diff(
    *,
    declared_hashes: dict[str, str],
    locked_hashes: dict[str, str],
) -> list[ScannerFinding]:
    """Compare a manifest's declared dependency hashes against a lockfile.

    When the manifest pins ``deps[<name>] = sha256/<hex>`` and the locked
    bundle disagrees, that's a strong supply-chain tampering signal (an
    attacker swapped the package post-publication).

    Args:
        declared_hashes: ``{name: "sha256/<hex>"}`` from the manifest.
        locked_hashes: ``{name: "sha256/<hex>"}`` from the lockfile in the
            bundle.

    Returns:
        Findings (severity ``HIGH``) for every mismatch.
    """
    findings: list[ScannerFinding] = []
    for name, declared in declared_hashes.items():
        locked = locked_hashes.get(name)
        if locked and declared and declared != locked:
            findings.append(
                ScannerFinding(
                    rule="dependency_hash_mismatch",
                    severity=ScannerSeverity.HIGH,
                    path="lockfile",
                    line=0,
                    message=(
                        f"dependency {name!r} declared {declared!r} but lockfile "
                        f"records {locked!r} - possible post-publication swap"
                    ),
                    cwe="CWE-494",
                    remediation=(
                        "rebuild the lockfile against the declared version; "
                        "if it still diverges, treat the bundle as untrusted"
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Internal scanning
# ---------------------------------------------------------------------------


def _scan_source(*, path: str, source: str) -> list[ScannerFinding]:
    """Run every rule against a single source file.

    Two-pass design: first compute file-level safe markers (the
    ``file_safe_pattern`` for each rule that has one), then per-line scan
    skips rules whose file-level marker is present. This avoids false
    positives where the allow-list check lives a few lines below the
    vulnerable assignment.
    """
    findings: list[ScannerFinding] = []
    file_safe_hits: dict[str, bool] = {
        rule.rule_id: bool(rule.file_safe_pattern is not None and rule.file_safe_pattern.search(source))
        for rule in _RULES
    }
    for line_no, line in enumerate(source.splitlines(), start=1):
        # Cheap pre-check: skip empty / pure-comment lines.
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "//")):
            continue
        for rule in _RULES:
            if file_safe_hits.get(rule.rule_id):
                continue
            if rule.pattern.search(line) is None:
                continue
            if rule.safe_pattern is not None and rule.safe_pattern.search(line):
                continue
            findings.append(
                ScannerFinding(
                    rule=rule.rule_id,
                    severity=rule.severity,
                    path=path,
                    line=line_no,
                    message=rule.message,
                    cwe=rule.cwe,
                    remediation=rule.remediation,
                )
            )
    return findings
