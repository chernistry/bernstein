#!/usr/bin/env python3
"""Drop SARIF results that carry a non-empty `suppressions` array.

Semgrep (and other linters) translate inline `# nosemgrep: <rule-id>`
markers into SARIF results with a populated `suppressions` array. GitHub
Code Scanning ingests those results regardless, surfacing phantom alerts
for code we have deliberately marked as not-an-issue. This filter strips
them out before upload so the Security tab matches local intent.

Usage::

    python3 scripts/sarif_drop_suppressed.py input.sarif > output.sarif
    python3 scripts/sarif_drop_suppressed.py input.sarif output.sarif
    cat input.sarif | python3 scripts/sarif_drop_suppressed.py > output.sarif

Behaviour:

- Results whose `suppressions` is a non-empty array are dropped.
- Results with missing `suppressions`, `null`, or `[]` are kept.
- Everything else (runs[*].tool, invocations, properties, ...) is
  preserved verbatim.
- Multi-run SARIF logs are handled.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _has_active_suppression(result: dict[str, Any]) -> bool:
    suppressions = result.get("suppressions")
    return isinstance(suppressions, list) and len(suppressions) > 0


def filter_sarif(sarif: dict[str, Any]) -> dict[str, Any]:
    """Return a new SARIF dict with suppressed results removed."""
    runs = sarif.get("runs")
    if not isinstance(runs, list):
        return sarif
    for run in runs:
        if not isinstance(run, dict):
            continue
        results = run.get("results")
        if not isinstance(results, list):
            continue
        run["results"] = [r for r in results if not (isinstance(r, dict) and _has_active_suppression(r))]
    return sarif


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) > 2:
        sys.stderr.write("usage: sarif_drop_suppressed.py [input.sarif [output.sarif]]\n")
        return 2
    in_path = args[0] if len(args) >= 1 else None
    out_path = args[1] if len(args) == 2 else None

    if in_path is None:
        raw = sys.stdin.read()
    else:
        with open(in_path, encoding="utf-8") as fh:
            raw = fh.read()

    sarif = json.loads(raw)
    filtered = filter_sarif(sarif)
    payload = json.dumps(filtered, indent=2)

    if out_path is None:
        sys.stdout.write(payload)
        sys.stdout.write("\n")
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
