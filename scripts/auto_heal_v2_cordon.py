"""CLI front-end for ``bernstein.core.autoheal.cordon``.

Usage::

    python scripts/auto_heal_v2_cordon.py <path> [--whitespace-only]

Exit code is 0 if the path is allowed under the cordon, 1 otherwise.
Used by the auto-heal v2 workflow so the YAML stays free of inline
Python (which is fragile inside block scalars).
"""

from __future__ import annotations

import argparse
import sys

from bernstein.core.autoheal.cordon import evaluate


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="auto_heal_v2_cordon")
    p.add_argument("path", help="repo-relative file path to check")
    p.add_argument(
        "--whitespace-only",
        action="store_true",
        help="declare the diff for this path as whitespace-only",
    )
    args = p.parse_args(argv)
    d = evaluate(args.path, whitespace_only=args.whitespace_only)
    if d.allowed:
        sys.stdout.write(f"OK {args.path} ({d.rule})\n")
        return 0
    sys.stderr.write(f"BLOCK {args.path} ({d.rule})\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
