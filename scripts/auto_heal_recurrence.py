"""Count closed-not-merged auto-heal PRs whose body mentions the same
autofix class as the current attempt.

Called by ``.github/workflows/auto-heal.yml`` to detect recurring
failures. Inputs:

* ``argv[1]``: path to a JSON file produced by ``gh pr list --json
  body,mergedAt`` (a list of objects).
* env ``NEEDLES``: whitespace-separated list of class names to look
  for. The matcher checks the bold marker ``**<class>**`` (the bullet
  list rendered by ``auto-heal.yml`` always uses bold markers, so a
  PR body with the bullet ``- **safe**: ...`` is matched on
  ``NEEDLES`` including ``safe``).

Prints a single integer to stdout: the count of matching PRs.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def count_recurrences(records: list[dict], needles: list[str]) -> int:
    """Return how many records are closed-but-not-merged AND whose body
    contains a bold marker for any of ``needles``.
    """
    if not needles:
        return 0
    count = 0
    for pr in records:
        if pr.get("mergedAt"):
            continue
        body = pr.get("body") or ""
        if any(f"**{n}**" in body for n in needles):
            count += 1
    return count


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: auto_heal_recurrence.py <json-path>\n")
        return 1
    path = Path(argv[1])
    if not path.exists():
        print(0)
        return 0
    raw = path.read_text().strip() or "[]"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(0)
        return 0
    needles = [n for n in (os.environ.get("NEEDLES", "").split()) if n]
    print(count_recurrences(data, needles))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
