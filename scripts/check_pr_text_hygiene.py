#!/usr/bin/env python
"""Pre-merge lint that scans pull-request text against a deny-list.

The script reads four surfaces of a pull request:

  - ``--title`` (string)
  - ``--body`` (string; markdown is fine, only substring scanning is done)
  - ``--branch`` (string; the head ref name)
  - ``--commit-messages-file`` (path to a file containing every commit
    subject + body in the PR, concatenated; the ``git log %B`` output
    with ``---`` separators is the expected format)

The deny-list lives in ``.github/pr-text-hygiene-deny.json`` so it can
evolve without code changes. Matching is plain case-insensitive
substring matching against each surface. Any match is reported via a
GitHub Actions ``::error::`` annotation on stdout and the script exits
with status 1; a clean run exits 0.

The script never reads PR labels. Label-based opt-out lives in the
workflow that calls this script (see
``.github/workflows/pr-text-hygiene.yml``).

Run locally::

    python scripts/check_pr_text_hygiene.py \\
      --title "ci: add foo" \\
      --body "" \\
      --branch "feat/foo" \\
      --commit-messages-file commit-msgs.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DENYLIST = _REPO_ROOT / ".github" / "pr-text-hygiene-deny.json"


def load_denylist(path: Path) -> list[str]:
    """Load the deny-list JSON file and return the list of phrases.

    The file must contain a top-level object with a ``denylist`` key
    whose value is a list of non-empty strings.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or "denylist" not in data:
        raise ValueError(f"deny-list file {path} missing top-level 'denylist' key")
    phrases = data["denylist"]
    if not isinstance(phrases, list):
        raise ValueError(f"deny-list file {path} 'denylist' must be a list")
    cleaned: list[str] = []
    for entry in phrases:
        if not isinstance(entry, str):
            raise ValueError(f"deny-list file {path} contains non-string entry: {entry!r}")
        stripped = entry.strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def scan_surface(surface: str, text: str, phrases: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``(surface, phrase)`` for each deny-list phrase that appears in *text*.

    Matching is case-insensitive substring matching. ``text`` may be
    empty or whitespace only; in that case no matches are returned.
    """
    if not text:
        return []
    lowered = text.lower()
    if not lowered.strip():
        return []
    findings: list[tuple[str, str]] = []
    for phrase in phrases:
        if phrase.lower() in lowered:
            findings.append((surface, phrase))
    return findings


def _read_commit_messages(path: Path) -> list[str]:
    """Split the commit-messages dump file into individual commit messages.

    The expected separator is a line containing only ``---``. Empty
    chunks are dropped.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    chunks = [chunk.strip() for chunk in text.split("\n---\n")]
    # Also tolerate a trailing standalone ``---`` token.
    cleaned: list[str] = []
    for chunk in chunks:
        stripped = chunk.strip().removesuffix("---").strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def check_pr_text(
    title: str,
    body: str,
    branch: str,
    commit_messages: list[str],
    phrases: list[str],
) -> list[tuple[str, str]]:
    """Run the full scan across every PR surface; return all findings."""
    findings: list[tuple[str, str]] = []
    findings.extend(scan_surface("title", title, phrases))
    findings.extend(scan_surface("body", body, phrases))
    findings.extend(scan_surface("branch", branch, phrases))
    for idx, message in enumerate(commit_messages):
        findings.extend(scan_surface(f"commit[{idx}]", message, phrases))
    return findings


def _emit_finding(surface: str, phrase: str) -> None:
    """Print a GitHub Actions annotation for a single match."""
    print(f"::error file={surface}::{phrase} matched in {surface}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", default="", help="PR title (may be empty).")
    parser.add_argument("--body", default="", help="PR body markdown (may be empty).")
    parser.add_argument("--branch", default="", help="PR head branch name (may be empty).")
    parser.add_argument(
        "--commit-messages-file",
        type=Path,
        default=None,
        help="Path to a file with every commit subject + body separated by '---' lines.",
    )
    parser.add_argument(
        "--denylist",
        type=Path,
        default=_DEFAULT_DENYLIST,
        help=f"Path to deny-list JSON (default: {_DEFAULT_DENYLIST}).",
    )
    args = parser.parse_args(argv)

    phrases = load_denylist(args.denylist)
    commit_messages: list[str] = []
    if args.commit_messages_file is not None:
        commit_messages = _read_commit_messages(args.commit_messages_file)

    findings = check_pr_text(
        title=args.title,
        body=args.body,
        branch=args.branch,
        commit_messages=commit_messages,
        phrases=phrases,
    )

    if not findings:
        print(f"check_pr_text_hygiene: OK ({len(phrases)} phrases, {len(commit_messages)} commit messages scanned)")
        return 0

    for surface, phrase in findings:
        _emit_finding(surface, phrase)
    print(
        f"check_pr_text_hygiene: FAIL ({len(findings)} match(es) across {len({s for s, _ in findings})} surface(s))",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
