"""Classify a hotfix chain against the R-counter benign-drift allow-list.

Used by ``.github/workflows/hotfix-r-tracker.yml`` to decide whether a
detected R>1 chain represents a genuine regression worth surfacing on
the parent feature PR, or whether it is the standard "agents-md sync
drift -> ruff format drift" cleanup sequence that happens after every
feature merge touching Python modules.

Contract
--------
- Reads commit subjects from stdin, one per line (oldest first; order
  does not matter for classification).
- Loads patterns from ``.github/r-counter-allowlist.txt`` (project
  root resolved from the script location).
- Exit 0 with stdout 'benign' when EVERY commit subject matches at
  least one allow-list pattern.
- Exit 0 with stdout 'investigate' when at least ONE commit subject
  fails to match any pattern.
- Exit 2 on usage / file errors so the workflow can fall back to the
  default (alert on R>1) instead of silently swallowing R-counter.

Example (workflow step):

  echo "$CHAIN_SUBJECTS" | python scripts/r_counter_classify.py
  # -> stdout: 'benign' or 'investigate'

The workflow then short-circuits on 'benign' (log step summary, no
PR comment) and proceeds as today on 'investigate'.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWLIST_PATH = Path(".github/r-counter-allowlist.txt")


def load_patterns(path: Path) -> list[re.Pattern[str]]:
    if not path.exists():
        raise FileNotFoundError(f"allow-list file missing: {path}")
    patterns: list[re.Pattern[str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line))
        except re.error as exc:
            raise ValueError(f"invalid regex in {path}: {line!r}: {exc}") from exc
    if not patterns:
        raise ValueError(f"allow-list {path} contains no patterns")
    return patterns


def classify(subjects: list[str], patterns: list[re.Pattern[str]]) -> str:
    """Return 'benign' if every subject matches at least one pattern,
    else 'investigate'. Empty input returns 'investigate' (defensive)."""
    if not subjects:
        return "investigate"
    for subject in subjects:
        if not any(p.search(subject) for p in patterns):
            return "investigate"
    return "benign"


def main() -> int:
    try:
        patterns = load_patterns(ALLOWLIST_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print(f"r_counter_classify: {exc}", file=sys.stderr)
        return 2
    subjects = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    verdict = classify(subjects, patterns)
    print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
