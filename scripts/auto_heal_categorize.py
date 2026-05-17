"""Classify failing CI job names into auto-heal safety classes.

Pure-Python categorizer consumed by ``.github/workflows/auto-heal.yml``.

Reads one job name per line from ``stdin`` and prints a JSON object of the
shape::

    {"safe": [...], "heuristic": [...], "risky": [...], "unknown": [...]}

Classes
-------
``safe``
    Deterministic mechanical autofix. Examples: ruff format/check --fix,
    ``bernstein agents-md sync``. Auto-heal applies the fix in-place.

``heuristic``
    Bounded auto-fix that needs a runtime check (parse logs, derive
    allowlist entries). The current implementation handles the
    ``Spelling (typos)`` job by extracting failing tokens and adding
    vendor-field-shaped ones to ``typos.toml``.

``risky``
    Anything that can fail for a thousand reasons (real tests, type
    checking, security scanners). Auto-heal refuses to touch these and
    emits a warning so a human can triage.

``unknown``
    Job name did not match any rule. Treated like ``risky`` -- safe
    default for an unrecognised failure.

The matchers are exact equality where possible. ``Test (...)`` matrix legs
are matched by the ``Test (`` prefix because the bracket suffix varies
across OS x Python combinations.
"""

from __future__ import annotations

import json
import sys
from typing import Final

# Exact-name allowlist for deterministic mechanical fixes.
_SAFE_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Lint",
        "Repo hygiene",
        "Dead code (Vulture)",
        "Snapshot tests (syrupy)",
        "Workflow lint",
    }
)

# Exact-name allowlist for heuristic fixes (log-driven, bounded).
_HEURISTIC_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Spelling (typos)",
    }
)

# Exact-name denylist for risky jobs (real test / security / type signal).
_RISKY_EXACT: Final[frozenset[str]] = frozenset(
    {
        "Type check",
        "Pyright strict (security + cluster)",
        "CodeQL",
        "Bandit (security)",
        "Semgrep (custom rules)",
        "Schemathesis smoke",
        "Mutation (diff-only)",
        "Property tests (Hypothesis smoke)",
        "Beartype (type contracts)",
        "Adapter integration (fake-CLI)",
        "Diff coverage gate",
        "pip-audit (deps)",
        "Package size check",
        "Lineage Gate",
        "Determine changes",
        "CI gate",
        "Auto-fix lint",
        "PR CI summary",
        "Close resolved CI issues",
    }
)

# Prefixes that mark a job as risky regardless of the bracketed suffix.
_RISKY_PREFIXES: Final[tuple[str, ...]] = ("Test (",)


def classify(job_name: str) -> str:
    """Return the safety class (``"safe"``, ``"heuristic"``, ``"risky"``,
    or ``"unknown"``) for one CI job name.

    Empty / whitespace-only names map to ``"unknown"``.
    """
    name = job_name.strip()
    if not name:
        return "unknown"
    if name in _SAFE_EXACT:
        return "safe"
    if name in _HEURISTIC_EXACT:
        return "heuristic"
    if name in _RISKY_EXACT:
        return "risky"
    for prefix in _RISKY_PREFIXES:
        if name.startswith(prefix):
            return "risky"
    return "unknown"


def categorize(job_names: list[str]) -> dict[str, list[str]]:
    """Group job names by safety class. Output order matches input order
    within each bucket; duplicates are preserved (caller decides on dedup).
    """
    buckets: dict[str, list[str]] = {
        "safe": [],
        "heuristic": [],
        "risky": [],
        "unknown": [],
    }
    for raw in job_names:
        name = raw.strip()
        if not name:
            continue
        buckets[classify(name)].append(name)
    return buckets


def main() -> int:
    """CLI entry point. Reads stdin, prints JSON to stdout, returns 0."""
    job_names = [line for line in sys.stdin.read().splitlines() if line.strip()]
    buckets = categorize(job_names)
    json.dump(buckets, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
