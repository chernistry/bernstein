#!/usr/bin/env python3
"""Review-bot acknowledgement gate.

Parses CodeRabbit and Sourcery comments on a PR, classifies each as
must-address (bug/security/potential-issue/refactor-with-correctness) or
informational (nit/style/note), and verifies every must-address finding is
either acknowledged in the PR body via a `<!-- bot-ack: <id> ... -->` marker
or addressed in a subsequent commit.

The gate posts a sticky summary comment on the PR (replacing any prior
summary it posted) and exits 1 if any must-address finding is unresolved.

Usage:
    python scripts/review_bot_ack.py \\
        --owner sipyourdrink-ltd --repo bernstein --pr 1576 [--strict]

Required environment:
    GH_TOKEN  GitHub token with `pull-requests: write` and `contents: read`.

Exit codes:
    0  Every must-address finding is fixed or acknowledged.
    1  At least one must-address finding is open.
    2  Internal error (HTTP failure, malformed JSON, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

REVIEW_BOT_LOGINS = {"coderabbitai[bot]", "sourcery-ai[bot]"}

# Tags that mark a finding as must-address. Matching is case-insensitive
# against the comment body. CodeRabbit uses headings like
# `**Potential issue**` or `_⚠️ Potential issue_`. Sourcery uses
# `**issue:**`, `**bug:**`, `**security:**`, `**suggestion (security):**`.
MUST_ADDRESS_PATTERNS = (
    r"potential issue",
    r"\bissue\b\s*:",
    r"\bbug\b\s*:",
    r"\bsecurity\b\s*:",
    r"suggestion\s*\(security\)",
    r"suggestion\s*\(bug",
    r"refactor\s*\(.*correctness",
    r"_⚠️\s*potential issue",
)

# Tags that mark a finding as informational. Anything that matches any
# informational pattern AND no must-address pattern is treated as skippable.
INFORMATIONAL_PATTERNS = (
    r"\bnit\b",
    r"\bstyle\b",
    r"\bnote\b\s*:",
    r"suggestion\s*\(style",
    r"suggestion\s*\(nit",
    r"suggestion\s*\(testing",
    r"refactor suggestion",
    r"\*\*note\*\*",
)

STICKY_HEADER = "<!-- review-bot-ack-summary: managed -->"
ACK_MARKER_RE = re.compile(
    r"<!--\s*bot-ack:\s*(?P<id>[\w./-]+)\s*(?:reason=(?P<reason>[^>]+?))?\s*-->",
    re.IGNORECASE,
)
NIT_BATCH_SKIP_RE = re.compile(r"<!--\s*bot-ack:\s*nit-batch-skipped\s*-->", re.IGNORECASE)


@dataclass
class Finding:
    comment_id: int
    author: str
    path: str | None
    body: str
    severity: str  # "must-address" | "informational"
    source: str  # "review-comment" | "issue-comment"
    html_url: str = ""

    @property
    def short(self) -> str:
        first = self.body.strip().splitlines()[0] if self.body.strip() else ""
        return first[:140]


@dataclass
class GateOutcome:
    findings: list[Finding] = field(default_factory=list)
    must_unresolved: list[Finding] = field(default_factory=list)
    must_acked: list[Finding] = field(default_factory=list)
    informational: list[Finding] = field(default_factory=list)


def gh_request(
    method: str,
    url: str,
    token: str,
    data: dict[str, Any] | None = None,
) -> Any:
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {exc.code} {body[:300]}") from exc
    if not body:
        return None
    return json.loads(body)


def paginate(url: str, token: str) -> list[Any]:
    out: list[Any] = []
    page = 1
    while True:
        full = f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}"
        chunk = gh_request("GET", full, token) or []
        if not isinstance(chunk, list):
            return out
        out.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
        if page > 30:
            break  # hard cap on pagination
    return out


def classify(body: str) -> str:
    low = body.lower()
    is_must = any(re.search(p, low) for p in MUST_ADDRESS_PATTERNS)
    is_info = any(re.search(p, low) for p in INFORMATIONAL_PATTERNS)
    if is_must and not is_info:
        return "must-address"
    if is_must and is_info:
        # When both tags appear, must-address wins so we don't lose a real bug.
        return "must-address"
    return "informational"


def fetch_findings(owner: str, repo: str, pr: int, token: str) -> list[Finding]:
    findings: list[Finding] = []
    base = f"https://api.github.com/repos/{owner}/{repo}"
    review = paginate(f"{base}/pulls/{pr}/comments", token)
    for c in review:
        login = (c.get("user") or {}).get("login", "")
        if login not in REVIEW_BOT_LOGINS:
            continue
        body = c.get("body") or ""
        findings.append(
            Finding(
                comment_id=int(c["id"]),
                author=login,
                path=c.get("path"),
                body=body,
                severity=classify(body),
                source="review-comment",
                html_url=c.get("html_url") or "",
            )
        )
    issues = paginate(f"{base}/issues/{pr}/comments", token)
    for c in issues:
        login = (c.get("user") or {}).get("login", "")
        if login not in REVIEW_BOT_LOGINS:
            continue
        body = c.get("body") or ""
        # Skip summary/review-guide blocks; they're not actionable findings.
        if "<!-- generated by sourcery-ai[bot]: start review_guide -->" in body.lower():
            continue
        if "summarize by coderabbit.ai" in body.lower() and "rate limit" in body.lower():
            continue
        if "summarize by coderabbit.ai" in body.lower() and "actionable comments posted: 0" in body.lower():
            continue
        sev = classify(body)
        if sev == "informational":
            # Top-level bot comments are usually summaries; only keep
            # informational records when explicitly actionable.
            continue
        findings.append(
            Finding(
                comment_id=int(c["id"]),
                author=login,
                path=None,
                body=body,
                severity=sev,
                source="issue-comment",
                html_url=c.get("html_url") or "",
            )
        )
    return findings


def pr_body(owner: str, repo: str, pr: int, token: str) -> str:
    data = gh_request("GET", f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr}", token)
    return (data or {}).get("body") or ""


def ack_ids(body: str) -> tuple[set[str], bool]:
    ids = {m.group("id") for m in ACK_MARKER_RE.finditer(body)}
    nit_batch = bool(NIT_BATCH_SKIP_RE.search(body))
    return ids, nit_batch


def fixup_addresses(owner: str, repo: str, pr: int, token: str) -> set[str]:
    """Return the set of comment IDs explicitly referenced by a fixup commit.

    A commit message containing `bot-ack: <id>` (or `addresses: <id>`) on the
    PR branch is treated as evidence that the finding was applied.
    """
    base = f"https://api.github.com/repos/{owner}/{repo}"
    commits = paginate(f"{base}/pulls/{pr}/commits", token)
    out: set[str] = set()
    pat = re.compile(r"bot-ack:\s*(\d+)|addresses:\s*(\d+)", re.IGNORECASE)
    for c in commits:
        msg = ((c.get("commit") or {}).get("message")) or ""
        for m in pat.finditer(msg):
            out.add(m.group(1) or m.group(2))
    return out


def evaluate(owner: str, repo: str, pr: int, token: str) -> GateOutcome:
    findings = fetch_findings(owner, repo, pr, token)
    body = pr_body(owner, repo, pr, token)
    acked, nit_batch = ack_ids(body)
    commit_acks = fixup_addresses(owner, repo, pr, token)
    out = GateOutcome(findings=findings)
    for f in findings:
        if f.severity == "informational":
            out.informational.append(f)
            continue
        if str(f.comment_id) in acked or str(f.comment_id) in commit_acks:
            out.must_acked.append(f)
        else:
            out.must_unresolved.append(f)
    # Informational findings can be cleared in one shot via nit-batch-skipped.
    if nit_batch:
        # Just informational; the marker is a documentation hint for humans.
        pass
    return out


def render_summary(outcome: GateOutcome) -> str:
    lines = [STICKY_HEADER, "## Review-bot acknowledgement summary", ""]
    total_must = len(outcome.must_unresolved) + len(outcome.must_acked)
    lines.append(
        f"- Must-address findings: **{total_must}** "
        f"({len(outcome.must_acked)} acknowledged, "
        f"{len(outcome.must_unresolved)} open)"
    )
    lines.append(f"- Informational findings: {len(outcome.informational)}")
    lines.append("")
    if outcome.must_unresolved:
        lines.append("### Open must-address findings")
        lines.append("")
        for f in outcome.must_unresolved:
            loc = f.path or "(general)"
            lines.append(f"- [{f.author}] `{loc}` (id `{f.comment_id}`): {f.short}")
        lines.append("")
        lines.append(
            "Each open finding must be either fixed in a fixup commit "
            "(`bot-ack: <id>` in the commit message) or acknowledged "
            "in the PR body with `<!-- bot-ack: <id> reason=... -->`."
        )
    else:
        lines.append("All must-address findings are resolved or acknowledged.")
    return "\n".join(lines).rstrip() + "\n"


def upsert_sticky(owner: str, repo: str, pr: int, token: str, body: str) -> None:
    base = f"https://api.github.com/repos/{owner}/{repo}"
    comments = paginate(f"{base}/issues/{pr}/comments", token)
    existing_id = None
    for c in comments:
        if STICKY_HEADER in (c.get("body") or ""):
            existing_id = c.get("id")
            break
    if existing_id is not None:
        gh_request(
            "PATCH",
            f"{base}/issues/comments/{existing_id}",
            token,
            data={"body": body},
        )
        return
    gh_request(
        "POST",
        f"{base}/issues/{pr}/comments",
        token,
        data={"body": body},
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True, type=int)
    p.add_argument(
        "--no-comment",
        action="store_true",
        help="Skip the sticky summary comment (for local runs).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Reserved; gate already fails on unresolved must-address.",
    )
    args = p.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("error: GH_TOKEN or GITHUB_TOKEN must be set", file=sys.stderr)
        return 2

    try:
        outcome = evaluate(args.owner, args.repo, args.pr, token)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = render_summary(outcome)
    print(summary)
    if not args.no_comment:
        try:
            upsert_sticky(args.owner, args.repo, args.pr, token, summary)
        except Exception as exc:
            print(f"warning: could not post sticky summary: {exc}", file=sys.stderr)

    return 1 if outcome.must_unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
