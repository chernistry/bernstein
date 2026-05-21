#!/usr/bin/env python
"""Targeted mutation tester for the lineage v1 modules.

The repo-wide mutmut config doesn't compose cleanly with our heavyweight
`tests/conftest.py` (it imports the whole orchestrator). Rather than
refactor the conftest, we run a focused set of well-known mutation
operators against the three lineage files and execute the lineage test
suite against each surviving mutation.

Operators applied (subset of mutmut's standard set):

  - `<`  <-> `<=` <-> `>=` <-> `>`           (comparison flips)
  - `==` <-> `!=`                              (equality flip)
  - `and` <-> `or`                             (boolean op flip)
  - `True` <-> `False`                         (constant flip)
  - `+= 1` -> `-= 1` (where applicable)
  - drop `not` from `not x`
  - `>= N` -> `> N` / `< N` -> `<= N`        (bound flips)

Targets:
  - src/bernstein/core/lineage/gate.py
  - src/bernstein/core/lineage/tips.py
  - src/bernstein/core/lineage/merge.py

A mutation is "killed" if the lineage test suite fails (exit != 0) when
that mutation is applied. Survival = the tests still pass with the
mutation, indicating a coverage gap.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGETS = [
    REPO / "src/bernstein/core/lineage/gate.py",
    REPO / "src/bernstein/core/lineage/tips.py",
    REPO / "src/bernstein/core/lineage/merge.py",
]
TEST_PATHS = [
    "tests/unit/lineage/",
]

# (search, replace) pairs applied one-at-a-time per line.
MUTATIONS: list[tuple[str, str]] = [
    (" < ", " <= "),
    (" <= ", " < "),
    (" > ", " >= "),
    (" >= ", " > "),
    (" == ", " != "),
    (" != ", " == "),
    (" and ", " or "),
    (" or ", " and "),
    ("True", "False"),
    ("False", "True"),
    ("not ", ""),
    (" 0", " 1"),
    (" 1", " 2"),
    (" 2", " 1"),
    ("[]", "[None]"),
    ("return True", "return False"),
    ("return False", "return True"),
    ("len(", "0 * len("),
]


def _run_tests() -> bool:
    """Returns True iff tests pass (mutation NOT killed)."""
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-x",
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            *TEST_PATHS,
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return res.returncode == 0


def _candidates(target: Path) -> list[tuple[int, str, str, str]]:
    """Return (line_no, original_line, search, replace) for each candidate.

    Skips:
      - blank lines
      - comments (#)
      - any line inside a triple-quoted docstring block
      - decorator argument lines (@dataclass(...), @click.command(...), etc.)
        - those are configuration knobs, not behaviour, so flipping True/False
        in them produces synthetic mutations that don't model real bugs.
    """
    out: list[tuple[int, str, str, str]] = []
    text = target.read_text().splitlines(keepends=True)
    in_docstring = False
    for i, line in enumerate(text):
        stripped = line.lstrip()
        # Track docstring blocks: a line containing """ toggles state unless
        # the line both opens and closes on the same line.
        triple_count = line.count('"""') + line.count("'''")
        if triple_count and triple_count % 2 == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        if stripped.startswith("@"):
            continue
        # Skip lines that are obviously decorator arg values across multiple
        # lines (e.g. inside `@dataclass(...)`).
        if line.rstrip().endswith(",") and "=" in line and "(" not in line:
            # heuristic: kwarg-only line inside a call
            continue
        for search, replace in MUTATIONS:
            if search in line and search != replace:
                out.append((i, line, search, replace))
    return out


def main() -> int:
    total = 0
    killed = 0
    survivors: list[str] = []
    timeouts = 0

    # Confirm baseline passes.
    print("[baseline] running lineage tests...", flush=True)
    if not _run_tests():
        print("Baseline tests fail; cannot run mutation testing.", file=sys.stderr)
        return 2
    print("[baseline] ok", flush=True)

    for target in TARGETS:
        original = target.read_text()
        candidates = _candidates(target)
        # Cap per-file mutations to keep wall-clock reasonable.
        candidates = candidates[:60]
        print(f"[{target.name}] {len(candidates)} mutation(s) to try", flush=True)

        for idx, (line_no, line, search, replace) in enumerate(candidates):
            total += 1
            new_line = line.replace(search, replace, 1)
            lines = original.splitlines(keepends=True)
            lines[line_no] = new_line
            try:
                target.write_text("".join(lines))
                try:
                    survived = _run_tests()
                except subprocess.TimeoutExpired:
                    timeouts += 1
                    # Treat as killed - infinite loop also a meaningful signal.
                    killed += 1
                    continue
                if survived:
                    survivors.append(
                        f"{target.relative_to(REPO)}:{line_no + 1} '{search}' -> '{replace}': {line.rstrip()}"
                    )
                    print(
                        f"  [{idx + 1}/{len(candidates)}] SURVIVED line {line_no + 1}",
                        flush=True,
                    )
                else:
                    killed += 1
                    if (idx + 1) % 10 == 0:
                        print(
                            f"  [{idx + 1}/{len(candidates)}] {killed}/{total} killed",
                            flush=True,
                        )
            finally:
                target.write_text(original)

    pct = (100 * killed / total) if total else 0
    print()
    print("=== Mutation report ===")
    print(f"Total mutations:  {total}")
    print(f"Killed:           {killed}")
    print(f"Survivors:        {len(survivors)}")
    print(f"Timeouts:         {timeouts}")
    print(f"Kill rate:        {pct:.1f}%")
    if survivors:
        print()
        print("Surviving mutations (coverage gaps):")
        for s in survivors:
            print(f"  - {s}")
    return 0 if pct >= 75 else 1


if __name__ == "__main__":
    sys.exit(main())
