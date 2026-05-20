"""Render the sticky observability comment posted on every PR.

Reads the JSON snapshot emitted by ``bernstein doctor observe --json``
and turns it into a compact Markdown block. Designed to run inside the
``pr-observability-summary.yml`` workflow but operator-runnable as well::

    bernstein doctor observe --json --no-persist > observe.json
    python scripts/observability/build_pr_summary.py \\
        --observe observe.json --pr 42 --branch feat/foo \\
        --out pr_summary.md

The output is intentionally terse: a one-row-per-backend status line, a
metrics table for backends that produced numeric rows, and a footer
pointing to the operator doc. Backends that soft-fail because their
credentials are not configured are folded into a single "skipped" line
so a fresh checkout does not generate noisy PR comments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_STATUS_ICON = {
    "ok": "OK",
    "warn": "WARN",
    "fail": "FAIL",
    "skipped": "skipped",
    "error": "ERROR",
}


def _load_observe(path: Path) -> dict[str, Any]:
    """Return the parsed payload or a synthetic skipped payload on error."""

    if not path.exists():
        return {
            "summary": {"skipped": 0, "ok": 0, "warn": 0, "fail": 0, "error": 1},
            "backends": [],
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "summary": {"skipped": 0, "ok": 0, "warn": 0, "fail": 0, "error": 1},
            "backends": [],
            "_parse_error": str(exc),
        }


def _render_header(pr: str, branch: str, summary: dict[str, int]) -> list[str]:
    line = (
        f"**bernstein doctor observe** for PR #{pr}"
        + (f" (`{branch}`)" if branch else "")
        + f": ok={summary.get('ok', 0)}, warn={summary.get('warn', 0)}, "
        f"fail={summary.get('fail', 0)}, error={summary.get('error', 0)}, "
        f"skipped={summary.get('skipped', 0)}"
    )
    return [line, ""]


def _render_backend(backend: dict[str, Any]) -> list[str]:
    name = backend.get("backend", "?")
    status = backend.get("status", "?")
    detail = backend.get("detail", "")
    metrics = backend.get("metrics") or []

    icon = _STATUS_ICON.get(status, status)
    header = f"### {name} -- {icon}"
    if detail:
        header += f" ({detail})"
    if not metrics:
        return [header, ""]

    lines = [
        header,
        "",
        "| metric | value | delta | threshold | status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for m in metrics:
        lines.append(
            "| {name} | {value} | {delta} | {threshold} | {status} |".format(
                name=m.get("name", ""),
                value=m.get("value", ""),
                delta=m.get("delta", "-"),
                threshold=m.get("threshold", "") or "-",
                status=m.get("threshold_status", ""),
            )
        )
    lines.append("")
    return lines


def _render_skipped_summary(skipped: list[str]) -> list[str]:
    if not skipped:
        return []
    return [
        "<details><summary>Skipped backends (credentials not configured)</summary>",
        "",
        "- " + "\n- ".join(skipped),
        "",
        "</details>",
        "",
    ]


def _build_markdown(payload: dict[str, Any], pr: str, branch: str) -> str:
    summary = payload.get("summary") or {}
    backends = payload.get("backends") or []
    lines = _render_header(pr=pr, branch=branch, summary=summary)
    skipped: list[str] = []
    for backend in backends:
        if backend.get("status") == "skipped":
            skipped.append(f"{backend.get('backend', '?')}: {backend.get('detail', '')}")
            continue
        lines.extend(_render_backend(backend))
    lines.extend(_render_skipped_summary(skipped))
    if payload.get("_parse_error"):
        lines.append(f"_observe JSON could not be parsed: {payload['_parse_error']}_")
        lines.append("")
    lines.append(
        "_See [docs/observability/unified-doctor.md]"
        "(/sipyourdrink-ltd/bernstein/blob/main/docs/observability/unified-doctor.md) "
        "for backend setup notes._"
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observe", required=True, type=Path, help="Path to observe JSON")
    parser.add_argument("--pr", required=True, help="Pull-request number")
    parser.add_argument("--branch", default="", help="Pull-request head branch")
    parser.add_argument("--out", required=True, type=Path, help="Output markdown path")
    args = parser.parse_args(argv)

    payload = _load_observe(args.observe)
    md = _build_markdown(payload, pr=args.pr, branch=args.branch)
    args.out.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
