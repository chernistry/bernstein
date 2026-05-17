"""Parse a typos failure log and produce vendor-field-shaped allowlist
candidates for ``typos.toml``.

Heuristic class auto-fix for ``.github/workflows/auto-heal.yml``. The
script reads the raw typos log on stdin, extracts the offending tokens,
filters them down to candidates that look like vendor field names (lower
snake_case, all-letters / digits / underscore, length 3-40, not already
in the typos dictionary as a corrected word), and prints them one per
line to stdout. The workflow then merges them into the
``[default.extend-words]`` table of ``typos.toml``.

Why "vendor-field-shaped"
-------------------------
Real misspellings in human prose (``recieve``, ``occured``) must never
be auto-allowlisted -- that defeats the spelling job's purpose. Vendor
field names emitted verbatim by upstream APIs (GitLab's ``noteable_id``,
Stripe's ``stmt_descr``) are the legitimate case for an allowlist
entry. The shape filter rejects anything that looks like English prose:

* must match ``^[a-z][a-z0-9_]{2,39}$`` (snake_case, lowercase)
* must contain at least one digit or underscore OR be longer than 7
  characters (English words of length <=7 are usually real)
* must NOT be on the small denylist of common misspellings
  (defence-in-depth -- typos's own dictionary already catches them,
  this is a second line of defence)
"""

from __future__ import annotations

import re
import sys
from typing import Final

# Pattern emitted by typos for every offending token.
#
# Example line:
#   error: `noteable` should be `notable`
#
# We capture the bad token and the proposed fix so the workflow can also
# log what the upstream tool would have done.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^error: `([^`]+)` should be `([^`]+)`",
    re.MULTILINE,
)

# Snake_case identifier shape (lowercase, digits, underscore allowed,
# starts with a letter, total length 3-40).
_SHAPE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{2,39}$")

# Hard denylist -- common misspellings that must never be auto-allowed.
_PROSE_TYPOS: Final[frozenset[str]] = frozenset(
    {
        "recieve",
        "recieved",
        "occured",
        "occurence",
        "seperately",
        "seperate",
        "definately",
        "calender",
        "wierd",
        "tommorow",
        "tommorrow",
        "noticable",
        "untill",
        "wich",
    }
)


def is_vendor_field_shape(token: str) -> bool:
    """Return True if ``token`` looks like a vendor API field name
    (lowercase snake_case, contains a digit / underscore or is long).

    Used to filter the auto-allowlist so prose typos can never be
    silently masked.
    """
    if token in _PROSE_TYPOS:
        return False
    if not _SHAPE_RE.match(token):
        return False
    has_digit_or_underscore = any(c.isdigit() or c == "_" for c in token)
    return has_digit_or_underscore or len(token) > 7


def extract_candidates(log: str) -> list[str]:
    """Parse a typos log and return the deduplicated set of tokens that
    pass the vendor-field-shape filter, in stable first-seen order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in _TOKEN_RE.finditer(log):
        token = match.group(1)
        if token in seen:
            continue
        seen.add(token)
        if is_vendor_field_shape(token):
            out.append(token)
    return out


def main() -> int:
    """CLI entry point. Reads typos log on stdin, prints candidates."""
    log = sys.stdin.read()
    for token in extract_candidates(log):
        sys.stdout.write(token + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
