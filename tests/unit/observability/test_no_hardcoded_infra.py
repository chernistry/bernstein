"""Regression guard: the shipped package hardcodes no specific observability host.

Observability integrations (error reporting, code-quality scan, SBOM,
telemetry) are deployment-time concerns. The package must be fully
functional with no observability backend configured and must never
default to any one host. This test scans the importable ``bernstein``
package source for forbidden host literals, the known fixed IP, and the
known DSN public-key id, and asserts zero matches so a default host can
never silently reappear.

Illustrative placeholders (``*.example.com``) are explicitly allowed:
they do not resolve and ship no real backend.
"""

from __future__ import annotations

from pathlib import Path

import bernstein

# Forbidden substrings. These are concrete observability backend hosts,
# the fixed server IP, and the DSN public-key id. None of these may be
# baked into the shipped package as a literal. Public product-schema
# identifiers (for example ``https://bernstein.run/schema/...``) are not
# backend hosts and are intentionally not listed here.
FORBIDDEN: tuple[str, ...] = (
    "errors.bernstein.run",
    "sonar.bernstein.run",
    "dt.bernstein.run",
    "dependency-track.bernstein.run",
    "telemetry.bernstein.run",
    "analytics.bernstein.run",
    "135.125.243.120",
    "798d55a9",
)


def _package_root() -> Path:
    """Return the on-disk root of the importable ``bernstein`` package."""
    pkg_file = Path(bernstein.__file__).resolve()
    return pkg_file.parent


def test_no_hardcoded_observability_infra() -> None:
    """No forbidden backend host / IP / DSN id appears in package source."""
    root = _package_root()
    offenders: list[str] = []

    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for needle in FORBIDDEN:
            if needle in text:
                offenders.append(f"{path}: contains {needle!r}")

    assert not offenders, (
        "Operator-private observability infrastructure must not be "
        "hardcoded in the shipped package. Offending references:\n  " + "\n  ".join(offenders)
    )


def _repo_root() -> Path:
    """Return the repository root (parent of ``src/`` and ``docs/``)."""
    return _package_root().parent.parent


def test_no_hardcoded_observability_infra_in_public_artefacts() -> None:
    """Docs and PR-comment-producing workflows must not leak operator-private hosts.

    The shipped package is already covered by the test above. This
    extends the same guard to the publishable surface: ``docs/`` (which
    renders on the project site) and ``.github/workflows/`` files that
    render strings into PR comments, GitHub issue bodies, or check-run
    output. A leak in any of these surfaces shows the operator's private
    observability hostnames to anyone reading a PR or visiting docs, so
    they are scrubbed and replaced with ``*.example.com`` placeholders
    or with operator-configured ``vars.*`` references.
    """
    repo = _repo_root()
    surfaces = [
        repo / "docs",
        repo / ".github" / "workflows",
    ]
    offenders: list[str] = []

    for surface in surfaces:
        if not surface.exists():
            continue
        for path in surface.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".md", ".mdx", ".yml", ".yaml", ".rst", ".txt"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for needle in FORBIDDEN:
                if needle in text:
                    offenders.append(f"{path}: contains {needle!r}")

    assert not offenders, (
        "Operator-private observability infrastructure must not appear in "
        "docs/ or in workflow files that render strings into PR comments "
        "or issue bodies. Use *.example.com placeholders in docs and "
        "operator-configured vars.* references in workflows. Offenders:\n  " + "\n  ".join(offenders)
    )
