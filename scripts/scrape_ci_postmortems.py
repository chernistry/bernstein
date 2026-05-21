#!/usr/bin/env python
"""Mine recently merged PRs for CI-failure post-mortems.

A merged pull request is treated as a CI-failure post-mortem when its
commit list shows a feature commit followed by 2+ fix-up commits. Each
such PR becomes one JSON record matching the ``CIFailurePostmortem``
schema in ``bernstein.eval.incident_synthesizer``.

Pipeline
--------
1. Use the ``gh`` CLI to list PRs merged in the last 30 days against
   the default branch.
2. For each PR, pull its commit subjects (oldest first).
3. Apply the fix-up regex (see :data:`FIXUP_SUBJECT_RE`) to every
   commit after the first. A PR qualifies when at least 2 of those
   trailing commits match.
4. Emit one JSON file per qualifying PR under
   ``.sdd/reports/ci_postmortems/pr-<PR#>-<short-sha>.json``.
5. Skip writing when a record for the same ``(pr_number, commit_sha)``
   already exists on disk or when an emitted YAML case already
   references the same source-incident key. This makes re-runs
   idempotent.

Fix-up commit heuristic
-----------------------
We deliberately keep the regex narrow so an honest fix-up author is
detected and unrelated commits are not swept in. A commit subject is
treated as a fix-up when it matches one of:

* ``fix(ci): ...`` / ``fix(tests): ...`` / ``fix(test): ...``
* ``fix(lint): ...`` / ``fix(types): ...`` / ``fix(typing): ...``
* ``fixup! ...`` / ``!fixup ...`` / ``squash! ...``
* Plain prefix ``fix ci:`` / ``fix tests:`` / ``fix lint:``

Operators that want a broader heuristic should adjust
:data:`FIXUP_SUBJECT_RE` here rather than override per-run. The
exact regex is operator-judgement territory: keep it explicit and
reviewed in this script.

Graceful degradation
--------------------
The scraper depends on the ``gh`` CLI. When ``gh`` is missing the
script writes a clear notice and exits 0; downstream integration
tests skip rather than fail. The same fallback fires when ``gh`` is
present but unauthenticated.

Usage::

    python scripts/scrape_ci_postmortems.py \\
        --repo sipyourdrink-ltd/bernstein \\
        --since-days 30 \\
        --out .sdd/reports/ci_postmortems

A ``--dry-run`` flag prints the JSON records to stdout without
touching the filesystem.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

# The scraper is intentionally importable without the bernstein package
# installed - that lets it run from a stale wheelhouse or air-gapped
# host. We *do* re-use the dataclass when bernstein is available so the
# scraper and the synthesizer never drift.
try:
    from bernstein.eval.incident_synthesizer import CIFailurePostmortem
except Exception:  # pragma: no cover - import fallback for air-gap
    CIFailurePostmortem = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger("scrape_ci_postmortems")

# Fix-up subject heuristic. Update only with reviewer eyes on the diff;
# it is the operator-judgement seam called out in the issue.
FIXUP_SUBJECT_RE: re.Pattern[str] = re.compile(
    r"""
    ^(?:
        fix\((?:ci|tests?|lint|types?|typing|format|formatting|coverage)\)\s*:
        | fixup!\s+
        | !fixup\s+
        | squash!\s+
        | fix\s+(?:ci|tests?|lint|typing|types)\s*:
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A PR qualifies when at least this many of its trailing (post-feature)
# commits are fix-up commits.
MIN_FIXUP_COMMITS: int = 2

# Common CI-failure error-line markers we surface verbatim. Conservative
# on purpose - the prompt readers only need a representative line.
_ERROR_LINE_HINTS: tuple[str, ...] = (
    "FAILED ",
    "AssertionError",
    "ruff check failed",
    "pyright",
    "mypy:",
    "E   ",
    "Error:",
    "error:",
)


def _gh_available() -> bool:
    """Return True when the ``gh`` CLI is on PATH and authenticated."""
    if shutil.which("gh") is None:
        return False
    try:
        proc = subprocess.run(
            ["gh", "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _gh_json(args: list[str]) -> Any:
    """Run a ``gh`` subcommand returning JSON; raise on failure."""
    proc = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        msg = f"gh {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}"
        raise RuntimeError(msg)
    if not proc.stdout.strip():
        return []
    return json.loads(proc.stdout)


def list_merged_prs(repo: str, since_days: int) -> list[dict[str, Any]]:
    """Return PRs merged in the last ``since_days`` days against the default branch."""
    args = [
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "merged",
        "--limit",
        "300",
        "--json",
        "number,title,mergeCommit,mergedAt,headRefName",
    ]
    raw = _gh_json(args)
    if not isinstance(raw, list):
        return []
    cutoff: float | None = None
    if since_days > 0:
        import datetime as _dt

        cutoff_dt = _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=since_days)
        cutoff = cutoff_dt.timestamp()

    fresh: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if cutoff is not None:
            merged_at = item.get("mergedAt") or ""
            ts = _parse_iso(merged_at)
            if ts is None or ts < cutoff:
                continue
        fresh.append(item)
    return fresh


def _parse_iso(value: str) -> float | None:
    if not value:
        return None
    try:
        import datetime as _dt

        # Accept both ``Z`` and offset suffixes.
        clean = value.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(clean).timestamp()
    except (TypeError, ValueError):
        return None


def list_pr_commits(repo: str, pr_number: int) -> list[str]:
    """Return commit subjects for a PR, oldest first."""
    args = [
        "pr",
        "view",
        str(pr_number),
        "--repo",
        repo,
        "--json",
        "commits",
    ]
    raw = _gh_json(args)
    if not isinstance(raw, dict):
        return []
    commits = raw.get("commits") or []
    subjects: list[str] = []
    for c in commits:
        if not isinstance(c, dict):
            continue
        # gh exposes the full message under ``messageHeadline`` (first
        # line) plus ``messageBody``. We only need the headline.
        subject = c.get("messageHeadline") or ""
        if isinstance(subject, str) and subject.strip():
            subjects.append(subject.strip())
    return subjects


def detect_fixup_commits(subjects: Iterable[str]) -> list[str]:
    """Apply the fix-up regex to every commit *after* the first.

    The first commit in a PR is treated as the original feature commit
    and never counted as a fix-up. Returns the list of qualifying
    subjects in input order.
    """
    out: list[str] = []
    for i, subj in enumerate(subjects):
        if i == 0:
            continue
        if FIXUP_SUBJECT_RE.match(subj):
            out.append(subj)
    return out


def _pick_error_line(text: str) -> str:
    """Pull a short representative error line from a free-form blob."""
    for line in text.splitlines():
        stripped = line.strip()
        if any(hint in stripped for hint in _ERROR_LINE_HINTS):
            return stripped
    return ""


def synthesize_record(
    pr: dict[str, Any],
    commits: list[str],
    fixups: list[str],
) -> dict[str, Any] | None:
    """Return a JSON-serialisable record or ``None`` when the PR does not qualify."""
    if len(fixups) < MIN_FIXUP_COMMITS:
        return None
    pr_number = pr.get("number")
    if not isinstance(pr_number, int):
        return None
    merge_commit = pr.get("mergeCommit") or {}
    commit_sha = ""
    if isinstance(merge_commit, dict):
        commit_sha = str(merge_commit.get("oid") or "")
    if not commit_sha:
        return None

    failing_test = _guess_failing_test(fixups)
    error_line = _pick_error_line("\n".join(fixups))

    record: dict[str, Any] = {
        "pr_number": pr_number,
        "commit_sha": commit_sha,
        "failing_test": failing_test,
        "error_line": error_line,
        "fixup_commits": fixups,
    }
    if CIFailurePostmortem is not None:
        # Round-trip through the dataclass to ensure the scraper and
        # synthesizer never drift on field names.
        pm = CIFailurePostmortem(
            pr_number=pr_number,
            commit_sha=commit_sha,
            failing_test=failing_test,
            error_line=error_line,
            fixup_commits=tuple(fixups),
        )
        record = asdict(pm)
        # ``asdict`` returns a tuple for ``fixup_commits``; JSON expects a list.
        record["fixup_commits"] = list(record["fixup_commits"])
    return record


_TEST_HINT_RE: re.Pattern[str] = re.compile(
    r"(?P<path>tests?/[A-Za-z0-9_/\-]+\.py(?:::[\w\[\].\-]+)*)",
)
_LINT_HINT_RE: re.Pattern[str] = re.compile(
    r"\b(ruff|pyright|mypy|black|isort|coverage|pre[\-_]commit)\b",
    re.IGNORECASE,
)


def _guess_failing_test(fixups: list[str]) -> str:
    """Try to extract a failing-test identifier from fix-up subjects."""
    joined = " ".join(fixups)
    m = _TEST_HINT_RE.search(joined)
    if m:
        return m.group("path")
    m = _LINT_HINT_RE.search(joined)
    if m:
        return m.group(1).lower()
    return ""


def existing_keys(out_dir: Path, cases_dir: Path | None) -> set[str]:
    """Return ``ci-postmortem:<pr>:<sha>`` keys already on disk.

    Two sources are merged:

    * The scraper's own ``out_dir`` (so re-running the script is a
      pure no-op).
    * Emitted YAML cases under ``cases_dir`` (so a previous synth pass
      that has already promoted a record into a YAML case is also
      treated as covered).
    """
    keys: set[str] = set()
    if out_dir.is_dir():
        for path in out_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pr_n = raw.get("pr_number") if isinstance(raw, dict) else None
            sha = raw.get("commit_sha") if isinstance(raw, dict) else None
            if isinstance(pr_n, int) and isinstance(sha, str) and sha:
                keys.add(f"ci-postmortem:{pr_n}:{sha}")
    if cases_dir and cases_dir.is_dir():
        for path in cases_dir.glob("inc-*.yaml"):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("source_incident:"):
                        raw_val = line.split(":", 1)[1].strip()
                        if len(raw_val) >= 2 and raw_val[0] == '"' and raw_val[-1] == '"':
                            raw_val = raw_val[1:-1]
                        if raw_val.startswith("ci-postmortem:"):
                            keys.add(raw_val)
                        break
            except OSError:
                continue
    return keys


def emit_record(record: dict[str, Any], out_dir: Path) -> Path:
    """Write a record under ``out_dir`` and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    short = str(record["commit_sha"])[:12]
    path = out_dir / f"pr-{record['pr_number']}-{short}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run(
    *,
    repo: str,
    since_days: int,
    out_dir: Path,
    cases_dir: Path | None,
    dry_run: bool,
    pr_data: list[dict[str, Any]] | None = None,
    commits_loader: Any = None,
) -> int:
    """Drive one scrape pass. Returns the number of records emitted."""
    if pr_data is None:
        if not _gh_available():
            logger.warning("gh CLI unavailable or unauthenticated; scraper exits 0 with no output")
            return 0
        pr_data = list_merged_prs(repo, since_days)

    loader = commits_loader or (lambda pr_num: list_pr_commits(repo, pr_num))
    seen = existing_keys(out_dir, cases_dir)
    emitted = 0
    for pr in pr_data:
        pr_number = pr.get("number")
        if not isinstance(pr_number, int):
            continue
        try:
            subjects = loader(pr_number)
        except Exception as exc:
            logger.warning("could not load commits for PR #%s: %s", pr_number, exc)
            continue
        fixups = detect_fixup_commits(subjects)
        record = synthesize_record(pr, subjects, fixups)
        if record is None:
            continue
        key = f"ci-postmortem:{record['pr_number']}:{record['commit_sha']}"
        if key in seen:
            continue
        if dry_run:
            print(json.dumps(record, sort_keys=True))
        else:
            path = emit_record(record, out_dir)
            logger.info("emitted %s", path)
        seen.add(key)
        emitted += 1
    return emitted


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--repo",
        default="sipyourdrink-ltd/bernstein",
        help="``owner/repo`` slug passed to ``gh``.",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Look back N days when listing merged PRs (default: 30).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".sdd/reports/ci_postmortems"),
        help="Output directory for emitted JSON records.",
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=Path("src/bernstein/eval/cases/incidents"),
        help="Existing YAML cases dir (consulted for dedup only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print records to stdout instead of writing files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    count = run(
        repo=args.repo,
        since_days=args.since_days,
        out_dir=args.out,
        cases_dir=args.cases_dir,
        dry_run=args.dry_run,
    )
    logger.info("scraper finished; %d new postmortem record(s)", count)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
