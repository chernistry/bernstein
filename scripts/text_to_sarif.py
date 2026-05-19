#!/usr/bin/env python3
"""Convert vulture / refurb / perflint text output to SARIF 2.1.0.

These three linters do not emit SARIF natively. The CI workflow
`static-analysis-extended.yml` pipes their text output into this script
so findings unify in the GitHub Code Scanning Security tab alongside
Semgrep, Trivy, CodeQL, and bandit.

Input formats supported:

- vulture:    "path:line: message (Nx confidence)"
- refurb:     "path:line:col [FURBxxx]: message" or "path:line:col: message"
- perflint:   pylint-style "path:line:col: Wxxxx: message (slug)"

Anything that does not match falls through to a generic
`path:line[:col]: rest-of-line` shape; lines that still do not match
are dropped (with a warning on stderr) rather than crashing the job.

Usage::

    python scripts/text_to_sarif.py --tool vulture --input vulture.txt \\
        --output vulture.sarif

The output is a SARIF 2.1.0 log with a single run, suitable for
`github/codeql-action/upload-sarif`. Severity defaults to ``warning``
which lines up with how Code Scanning displays advisory findings.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

SARIF_VERSION: Final = "2.1.0"
SARIF_SCHEMA: Final = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/Schemata/sarif-schema-2.1.0.json"

# vulture: "path:line: msg (Nx confidence)"
_VULTURE_RE = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):\s*(?P<msg>.+)$")

# refurb: "path:line:col [FURBxxx]: msg"  or  "path:line:col: msg"
_REFURB_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+)\s*"
    r"(?:\[(?P<code>FURB\d+)\])?\s*:?\s*(?P<msg>.+)$"
)

# perflint (pylint style): "path:line:col: Wxxxx: msg (slug)"
_PERFLINT_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*"
    r"(?P<code>[A-Z]\d+):\s*(?P<msg>.+)$"
)

# Generic fallback: "path:line[:col]: rest"
_GENERIC_RE = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<msg>.+)$")


@dataclass
class Finding:
    """Normalised lint finding ready for SARIF emission."""

    rule_id: str
    message: str
    path: str
    line: int
    column: int = 1
    level: str = "warning"


@dataclass
class ToolMeta:
    """Per-tool SARIF tool metadata."""

    name: str
    information_uri: str
    rules: dict[str, dict[str, object]] = field(default_factory=dict)


_TOOL_META: Final[dict[str, ToolMeta]] = {
    "vulture": ToolMeta(
        name="vulture",
        information_uri="https://github.com/jendrikseipp/vulture",
    ),
    "refurb": ToolMeta(
        name="refurb",
        information_uri="https://github.com/dosisod/refurb",
    ),
    "perflint": ToolMeta(
        name="perflint",
        information_uri="https://github.com/tonybaloney/perflint",
    ),
}


def _parse_line(tool: str, raw: str) -> Finding | None:
    """Parse one output line; return ``None`` on no-match."""
    line = raw.rstrip("\n").rstrip("\r").strip()
    if not line:
        return None

    if tool == "vulture":
        m = _VULTURE_RE.match(line)
        if not m:
            return None
        return Finding(
            rule_id="vulture/dead-code",
            message=m.group("msg").strip(),
            path=m.group("path"),
            line=int(m.group("line")),
        )

    if tool == "refurb":
        m = _REFURB_RE.match(line)
        if not m:
            return None
        code = m.group("code") or "refurb/idiom"
        return Finding(
            rule_id=code,
            message=m.group("msg").strip(),
            path=m.group("path"),
            line=int(m.group("line")),
            column=int(m.group("col") or 1),
        )

    if tool == "perflint":
        m = _PERFLINT_RE.match(line)
        if not m:
            return None
        return Finding(
            rule_id=f"perflint/{m.group('code')}",
            message=m.group("msg").strip(),
            path=m.group("path"),
            line=int(m.group("line")),
            column=int(m.group("col") or 1),
        )

    # Generic fallback.
    m = _GENERIC_RE.match(line)
    if not m:
        return None
    return Finding(
        rule_id=f"{tool}/generic",
        message=m.group("msg").strip(),
        path=m.group("path"),
        line=int(m.group("line")),
        column=int(m.group("col") or 1),
    )


def _to_sarif(tool: str, findings: list[Finding]) -> dict[str, object]:
    """Build a SARIF 2.1.0 log from the parsed findings."""
    meta = _TOOL_META.get(tool) or ToolMeta(
        name=tool,
        information_uri="https://example.invalid/",
    )

    rules: dict[str, dict[str, object]] = {}
    for f in findings:
        rules.setdefault(
            f.rule_id,
            {
                "id": f.rule_id,
                "shortDescription": {"text": f.rule_id},
                "fullDescription": {"text": f.rule_id},
                "defaultConfiguration": {"level": f.level},
            },
        )

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": meta.name,
                        "informationUri": meta.information_uri,
                        "rules": list(rules.values()),
                    },
                },
                "results": [
                    {
                        "ruleId": f.rule_id,
                        "level": f.level,
                        "message": {"text": f.message},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {
                                        "uri": f.path,
                                        "uriBaseId": "%SRCROOT%",
                                    },
                                    "region": {
                                        "startLine": f.line,
                                        "startColumn": f.column,
                                    },
                                },
                            },
                        ],
                    }
                    for f in findings
                ],
            },
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tool",
        choices=sorted(_TOOL_META.keys()),
        required=True,
        help="Source tool that produced the input.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the tool's text output. '-' means stdin.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the SARIF log to.",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if str(args.input) == "-" else args.input.read_text(encoding="utf-8", errors="replace")

    findings: list[Finding] = []
    skipped = 0
    for raw_line in raw.splitlines():
        parsed = _parse_line(args.tool, raw_line)
        if parsed is None:
            if raw_line.strip():
                skipped += 1
            continue
        findings.append(parsed)

    if skipped:
        print(
            f"text_to_sarif: skipped {skipped} unparseable line(s) from {args.tool} output",
            file=sys.stderr,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_to_sarif(args.tool, findings), indent=2),
        encoding="utf-8",
    )
    print(
        f"text_to_sarif: wrote {len(findings)} finding(s) to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
