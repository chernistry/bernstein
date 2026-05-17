"""Single entry point invoked by ``.github/workflows/auto-heal.yml`` (v2).

Subcommands map 1:1 to workflow steps so the YAML stays declarative
and unit tests can target each phase in isolation.

Subcommands
-----------

``triage``       Read failing job names from stdin, write a JSON
                 bucket map (safe / heuristic / risky / unknown) to
                 stdout, and write a one-line summary to
                 ``$GITHUB_STEP_SUMMARY`` when present.

``check-kill-switch`` Read ``.sdd/autoheal-disabled``; exit 0 if
                      enabled (continue), exit 1 if disabled with a
                      one-line stderr reason.

``select-strategy`` Read a comma-separated candidate list, draw one
                    Thompson sample per strategy from the persisted
                    bandit, print the winner to stdout.

``record-outcome``  Update bandit + Bayesian state given a strategy
                    name and success/failure flag passed as args.

``log``             Append one HealRecord to the audit ledger using
                    JSON args from stdin.

All subcommands write only to ``.sdd/`` (gitignored) and stdout. They
never touch the working tree directly; the workflow stays in charge
of git operations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from bernstein.core.autoheal import (
    bandit,
    bayesian,
    categorizer,
    kill_switch,
    wire,
)
from bernstein.core.autoheal.audit_log import HealRecord

DEFAULT_SDD_DIR = Path(".sdd")


def _sdd_path(*parts: str) -> Path:
    """Resolve a path under ``.sdd/`` (or ``$BERNSTEIN_SDD_DIR`` for tests)."""
    root_env = os.environ.get("BERNSTEIN_SDD_DIR")
    root = Path(root_env) if root_env else DEFAULT_SDD_DIR
    return root.joinpath(*parts)


def cmd_triage(_args: argparse.Namespace) -> int:
    """Categorise failing job names read from stdin."""
    names = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    bucketed = categorizer.bucketize(names)
    out = {
        "safe": list(bucketed.safe),
        "heuristic": list(bucketed.heuristic),
        "risky": list(bucketed.risky),
        "unknown": list(bucketed.unknown),
        "should_heal": bucketed.should_heal(),
    }
    json.dump(out, sys.stdout, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_check_kill_switch(_args: argparse.Namespace) -> int:
    """Exit non-zero if the kill switch is engaged."""
    state = kill_switch.read(_sdd_path("autoheal-disabled"))
    if state.disabled:
        sys.stderr.write(f"autoheal disabled: {state.reason}\n")
        return 1
    sys.stdout.write(f"autoheal enabled ({state.reason})\n")
    return 0


def cmd_select_strategy(args: argparse.Namespace) -> int:
    """Pick a strategy via Thompson sampling."""
    candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
    if not candidates:
        sys.stderr.write("no candidate strategies provided\n")
        return 2
    state = bandit.load_state(_sdd_path("autoheal-bandit.json"))
    chosen = state.select(candidates)
    sys.stdout.write(chosen + "\n")
    return 0


def cmd_record_outcome(args: argparse.Namespace) -> int:
    """Update bandit + Bayesian posteriors with one observation."""
    success = args.outcome.lower() in ("success", "applied", "true", "1")

    bandit_path = _sdd_path("autoheal-bandit.json")
    bstate = bandit.load_state(bandit_path)
    bstate.record(args.strategy, success=success)
    bandit.save_state(bstate, bandit_path)

    bayes_path = _sdd_path("autoheal-bayes.json")
    bystate = bayesian.load(bayes_path)
    cls = args.cls
    if cls not in ("safe", "heuristic", "risky", "unknown"):
        cls = "unknown"
    bystate.update(cls, args.job, success=success)  # type: ignore[arg-type]
    bayesian.save(bystate, bayes_path)
    return 0


def cmd_log(_args: argparse.Namespace) -> int:
    """Append one HealRecord from stdin JSON to the ledger.

    Also mirrors the row to the decision log (kind ``autoheal_strategy``)
    and the calibration log so ``bernstein decisions tail`` and the
    weekly Brier report include autoheal actions. All sidecar writes
    are best-effort: failures are warned and the audit append still
    proceeds.
    """
    try:
        body = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"invalid JSON on stdin: {e}\n")
        return 2
    if not isinstance(body, dict):
        sys.stderr.write("expected a JSON object on stdin\n")
        return 2

    candidates_raw = body.get("candidates", [])
    candidates: tuple[str, ...] = (
        tuple(str(c) for c in candidates_raw if str(c).strip()) if isinstance(candidates_raw, list) else ()
    )

    result = wire.record_heal(
        run_id=str(body.get("run_id", "")),
        head_sha=str(body.get("head_sha", "")),
        strategy=str(body.get("strategy", "")),
        cls=str(body.get("cls", "unknown")),
        confidence=float(body.get("confidence", 0.0)),
        outcome=str(body.get("outcome", "skipped_no_jobs")),
        cost_usd=float(body.get("cost_usd", 0.0)),
        llm_calls=int(body.get("llm_calls", 0)),
        patch_sha=str(body.get("patch_sha", "")),
        rationale=str(body.get("rationale", "")),
        candidates=candidates,
        sdd_dir=_sdd_path(),
    )
    if not result.audit_written:
        sys.stderr.write("warning: audit ledger write failed\n")
    sys.stdout.write(
        json.dumps(
            {
                "decision_id": result.decision_id,
                "decision_log": result.decision_log_written,
                "calibration": result.calibration_written,
                "audit": result.audit_written,
            },
            sort_keys=True,
        )
        + "\n",
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="auto_heal_v2_run", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("triage", help="categorise failing job names from stdin")
    sub.add_parser("check-kill-switch", help="exit 1 if autoheal is disabled")

    sel = sub.add_parser("select-strategy", help="thompson-sample a strategy")
    sel.add_argument("--candidates", required=True, help="comma-separated names")

    rec = sub.add_parser("record-outcome", help="update bandit + bayesian state")
    rec.add_argument("--strategy", required=True)
    rec.add_argument("--cls", required=True)
    rec.add_argument("--job", required=True)
    rec.add_argument("--outcome", required=True)

    sub.add_parser("log", help="append a HealRecord from stdin JSON")
    return p


_DISPATCH = {
    "triage": cmd_triage,
    "check-kill-switch": cmd_check_kill_switch,
    "select-strategy": cmd_select_strategy,
    "record-outcome": cmd_record_outcome,
    "log": cmd_log,
}


def main(argv: list[str] | None = None) -> int:
    """Module entry point."""
    args = _build_parser().parse_args(argv)
    handler = _DISPATCH[args.command]
    return handler(args)


__all__ = [
    "HealRecord",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
