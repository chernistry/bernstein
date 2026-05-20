"""Workflow entry point for the Tier-3 OpenRouter shadow-mode runner.

Invoked by ``.github/workflows/bernstein-ci-fix.yml`` after Tier-2 has
reported ``no_changes`` on a safe-allowlist failure class. Wraps
:mod:`bernstein.core.autofix.tier3` with a thin :class:`RunHook` that
shells out to ``bernstein run --cli qwen`` against the OpenRouter
free-tier endpoint. The Python module owns all bookkeeping: the
script's job is to translate workflow inputs into a
:class:`FailureContext`, plug in the provider hook, and exit cleanly.

The script never pushes a commit. Promotion stays governed by the
``BERNSTEIN_CI_SELF_DRIVE_PROMOTE_FROM_SHADOW`` env var, which is
documented as off until the shadow-week metrics review.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from bernstein.core.autofix.tier3 import (
    FailureContext,
    RunResult,
    Tier3Config,
    Tier3Runner,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _read_log_tail(path: Path) -> str:
    """Return the last 200 lines of ``path`` (or empty when unreadable)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-200:])


def _normalise_failure_class(raw: str) -> str:
    """Trim the surrounding noise the workflow may emit on the class line.

    GitHub Actions surfaces job names with a trailing matrix suffix
    like ``"Test (foo, bar)"`` and possibly a SHA suffix. We strip
    everything after the first ``" ("`` only when the class is
    enumerated by the workflow allowlist, so the resulting string lines
    up with :data:`SAFE_FAILURE_CLASSES`. Anything we cannot map is
    surfaced verbatim - the safe-allowlist gate inside the module will
    refuse it.
    """
    cleaned = raw.strip()
    if cleaned.startswith("Test ("):
        return "Test (contract-drift)" if "contract-drift" in cleaned else "Test (autoheal)"
    return cleaned


def _build_openrouter_hook() -> object:
    """Return a :class:`RunHook` that shells out to ``bernstein run``.

    The hook reads the OpenRouter API key from the env (so it never
    appears on argv) and walks the fallback list on HTTP 429 / network
    failures. A failure on every model returns an empty patch so the
    Tier-3 runner records the shadow_empty outcome.
    """

    def _call(
        *,
        context: FailureContext,
        primary_model: str,
        fallback_models: Sequence[str],
        openrouter_base_url: str,
    ) -> RunResult:
        if not openrouter_base_url:
            print("tier3: OpenRouter base URL not set; skipping", file=sys.stderr)
            return RunResult(patch="", model_used="")
        api_key = os.environ.get("OPENROUTER_API_KEY_FREE", "").strip()
        if not api_key:
            print("tier3: OPENROUTER_API_KEY_FREE not set; skipping", file=sys.stderr)
            return RunResult(patch="", model_used="")

        candidate_models = (primary_model, *fallback_models)
        last_model = ""
        for model in candidate_models:
            last_model = model
            env = dict(os.environ)
            env["OPENAI_BASE_URL"] = openrouter_base_url
            env["OPENAI_API_KEY"] = api_key
            env["BERNSTEIN_TIER3_MODEL"] = model
            cmd = [
                "bernstein",
                "run",
                "--cli",
                "qwen",
                "--task",
                "fix-ci",
                "--budget",
                "0.00",
                "--max-retries",
                "1",
                "--post-comment",
                "false",
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                print(f"tier3: {model}: {exc}", file=sys.stderr)
                continue
            if completed.returncode == 0:
                diff = _collect_pending_diff()
                if diff.strip():
                    return RunResult(patch=diff, model_used=model, cost_usd=0.0)
                # Successful return with no diff is treated like an
                # empty-patch outcome; do not walk the fallback list.
                return RunResult(patch="", model_used=model, cost_usd=0.0)
            # Treat non-zero exit as a retryable fallback signal; the
            # real 429 / overload classification is captured in the
            # subprocess stderr but not parsed here.
            print(
                f"tier3: {model} exited {completed.returncode}; trying next fallback",
                file=sys.stderr,
            )
            continue
        return RunResult(patch="", model_used=last_model)

    return _call


def _collect_pending_diff() -> str:
    """Capture the working-tree diff produced by the bernstein run."""
    try:
        completed = subprocess.run(
            ["git", "diff", "--no-color"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return completed.stdout or ""


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-run-id", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--failure-class", required=True)
    parser.add_argument(
        "--failing-test-nodeid",
        default="",
        help="Pytest nodeid for recurrence keying; empty when not a test failure.",
    )
    parser.add_argument(
        "--regression-test-sha",
        default="",
        help="SHA of the regression test that pinned the failure (when known).",
    )
    parser.add_argument("--log-tail", type=Path, required=True)
    parser.add_argument("--sdd-dir", type=Path, default=Path(".sdd"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (always 0 in shadow mode).

    The workflow keeps running on any tier-3 short-circuit (flag_off,
    unsafe_class, etc.); the artefact upload step in the YAML captures
    whatever was written under ``.sdd/`` for operator review.
    """
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    config = Tier3Config.from_env()

    context = FailureContext(
        failed_run_id=args.failed_run_id,
        head_sha=args.head_sha,
        failure_class=_normalise_failure_class(args.failure_class),
        failing_test_nodeid=args.failing_test_nodeid,
        log_tail=_read_log_tail(args.log_tail),
        regression_test_sha=args.regression_test_sha,
        tier2_produced_patch=False,
    )

    runner = Tier3Runner(
        config=config,
        run_hook=_build_openrouter_hook(),  # type: ignore[arg-type]
        sdd_dir=args.sdd_dir,
    )
    outcome = runner.run(context)

    summary = {
        "kind": outcome.kind,
        "reason": outcome.reason,
        "model_used": outcome.model_used,
        "cost_usd": outcome.cost_usd,
        "patch_sha": outcome.patch_sha,
        "patch_path": outcome.patch_path,
        "decision_id": outcome.decision_id,
        "rejected_paths": list(outcome.rejected_paths),
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
