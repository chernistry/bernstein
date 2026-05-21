#!/usr/bin/env python
"""Lineage CI gate entry point (ADR-009 §6.2).

Runs `bernstein.core.lineage.gate.check` against `.sdd/lineage/log.jsonl`
and prints a structured report. Exit codes:

  0 = log absent, OR all invariants pass.
  1 = at least one invariant failed.

Designed for `.github/workflows/ci.yml` - the "Lineage Gate" required
check shells into this script with no arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from bernstein.core.lineage.gate import GateResult, check


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify lineage log invariants.")
    p.add_argument(
        "--log",
        type=Path,
        default=Path(".sdd/lineage/log.jsonl"),
        help="Path to log.jsonl (default: .sdd/lineage/log.jsonl)",
    )
    p.add_argument(
        "--cards",
        type=Path,
        default=Path(".sdd/agents"),
        help="Agent cards directory (default: .sdd/agents)",
    )
    p.add_argument(
        "--steward-allowlist",
        default=None,
        help="Comma-separated list of agent_ids permitted to write merge entries.",
    )
    p.add_argument(
        "--operator-secret-env",
        default="BERNSTEIN_OPERATOR_SECRET",
        help="Env var name carrying the operator HMAC secret (optional).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    return p.parse_args(argv)


def _emit_report(result: GateResult, *, machine: bool) -> None:
    if machine:
        print(json.dumps({"ok": result.ok, "failures": result.failures}, indent=2))
        return
    if result.ok:
        print("Lineage gate: PASS")
        return
    print(f"Lineage gate: FAIL ({len(result.failures)} issue(s))", file=sys.stderr)
    for fail in result.failures:
        print(f"  - {fail}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not args.log.exists():
        if args.json:
            print(json.dumps({"ok": True, "failures": [], "skipped": "log missing"}))
        else:
            print(f"Lineage gate: SKIP (no log at {args.log})")
        return 0

    allowlist: frozenset[str] | None = None
    if args.steward_allowlist:
        allowlist = frozenset(s.strip() for s in args.steward_allowlist.split(",") if s.strip())

    operator_secret: bytes | None = None
    secret_str = os.environ.get(args.operator_secret_env)
    if secret_str:
        operator_secret = secret_str.encode("utf-8")

    result = check(
        log_path=args.log,
        agent_cards_dir=args.cards,
        operator_secret=operator_secret,
        steward_allowlist=allowlist,
    )
    _emit_report(result, machine=args.json)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
