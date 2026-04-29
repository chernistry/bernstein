#!/usr/bin/env python3
"""PyPI install load-test helper.

Two modes:

* ``run`` — perform N installs of bernstein in a sequential loop locally
  and emit a chunk-style result JSON. Useful for sanity-checking the
  install path and the timing measurements before triggering a full
  Actions run that fans out across hundreds of matrix jobs.

* ``report`` — combine chunk-level result JSONs (produced either by this
  script's ``run`` mode or by the matrix jobs in
  ``.github/workflows/pypi-install-load-test.yml``) plus pre/post
  pypistats snapshots into a Markdown + JSON report.

Used both by the workflow's aggregator job and by humans for local
debugging. Examples::

    # Local smoke test — installs bernstein 20 times sequentially
    python dev/tools/pypi_load_test.py run --installs 20

    # Render a report from chunk JSONs the workflow already gathered
    python dev/tools/pypi_load_test.py report \\
        --results results/ \\
        --pre pre_stats.json \\
        --post post_stats.json \\
        --installs 500 --concurrency 80 --version latest \\
        --md report.md --json report.json

The ``run`` mode produces a result JSON in the exact shape the workflow's
matrix jobs emit, so a chunk produced locally drops cleanly into a
multi-chunk aggregation by the ``report`` mode.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

# ---------------------------------------------------------------------------
# run mode
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    """Run ``args.installs`` sequential installs and write a chunk JSON.

    Each install creates a fresh venv, runs ``uv pip install --no-cache
    --no-deps bernstein`` against it, times the call, and immediately tears
    the venv down. Failures are counted, not propagated — the chunk JSON
    captures success/failure counts and a sample of error tails.
    """
    pkg = "bernstein"
    if args.version:
        pkg = f"bernstein=={args.version}"

    use_uv = shutil.which("uv") is not None
    if not use_uv:
        print(
            "warning: `uv` not found on PATH; falling back to `python -m venv` "
            "+ `pip install`. Output is correct but slower.",
            file=sys.stderr,
        )

    durations_ms: list[int] = []
    error_lines: list[str] = []
    success = 0
    failure = 0

    tmp_root = Path(args.tmp)
    tmp_root.mkdir(parents=True, exist_ok=True)

    print(f"Running {args.installs} install(s) of {pkg} (sequential)…", flush=True)
    chunk_t0 = time.monotonic_ns()

    for i in range(args.installs):
        venv = tmp_root / f"venv-{i}"
        # Belt-and-suspenders: ensure we don't accidentally reuse a leftover
        # venv from a previous run, which would skew the timing.
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)

        try:
            if use_uv:
                _run(["uv", "venv", "--quiet", "--python", "3.12", str(venv)])
            else:
                _run([sys.executable, "-m", "venv", str(venv)])
        except subprocess.CalledProcessError as exc:
            failure += 1
            error_lines.append(f"#{i}: venv-create failed: {exc.stderr.strip()[:200]}")
            print(f"  [{i + 1}/{args.installs}] FAIL (venv): see report", flush=True)
            continue

        py = venv / "bin" / "python"
        install_t0 = time.monotonic_ns()
        if use_uv:
            cmd = [
                "uv",
                "pip",
                "install",
                "--python",
                str(py),
                "--no-cache",
                "--no-deps",
                "--quiet",
                pkg,
            ]
        else:
            cmd = [
                str(py),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--no-deps",
                "--quiet",
                pkg,
            ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        install_t1 = time.monotonic_ns()

        if proc.returncode == 0:
            success += 1
            elapsed_ms = (install_t1 - install_t0) // 1_000_000
            durations_ms.append(elapsed_ms)
            print(
                f"  [{i + 1}/{args.installs}] ok in {elapsed_ms:>6} ms",
                flush=True,
            )
        else:
            failure += 1
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            error_lines.append(f"#{i}: " + " | ".join(tail)[:200])
            preview = tail[-1][:80] if tail else "?"
            print(f"  [{i + 1}/{args.installs}] FAIL: {preview}", flush=True)

        shutil.rmtree(venv, ignore_errors=True)

    chunk_t1 = time.monotonic_ns()
    wall_ms = (chunk_t1 - chunk_t0) // 1_000_000

    result = {
        "chunk": args.chunk,
        "success": success,
        "failure": failure,
        "wall_ms": wall_ms,
        "durations_ms": durations_ms,
        # Match the workflow's shape: errors is a single string, not a list,
        # so jq --arg in the bash side stays simple.
        "errors": "\n".join(error_lines)[:2000],
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")

    print()
    print(f"Wrote {out} — success={success}  failure={failure}  wall={wall_ms} ms")
    if durations_ms:
        avg = round(mean(durations_ms))
        print(f"Per-install: avg={avg} ms  min={min(durations_ms)}  max={max(durations_ms)}")

    return 0 if failure == 0 else 1


def _run(cmd: list[str]) -> None:
    """Run a command, raising CalledProcessError with captured stderr on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)


# ---------------------------------------------------------------------------
# report mode
# ---------------------------------------------------------------------------


def _percentile(data: list[int], pct: float) -> int | None:
    """Nearest-rank percentile. Returns None for empty input."""
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    sorted_data = sorted(data)
    k = max(0, min(len(sorted_data) - 1, round((pct / 100) * (len(sorted_data) - 1))))
    return sorted_data[k]


def _fmt_int(value: int | None) -> str:
    return f"{value:,}" if value is not None else "—"


def _fmt_ms(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    return f"{value} ms"


def _delta(after: int | None, before: int | None) -> str:
    if after is None or before is None:
        return "—"
    diff = after - before
    if diff == 0:
        return "0"
    return f"{diff:+,}"


def _recent(blob: dict[str, Any], key: str) -> int | None:
    try:
        value = blob.get("recent", {}).get("data", {}).get(key)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _overall(blob: dict[str, Any], category: str) -> int | None:
    try:
        for row in blob.get("overall", {}).get("data", []) or []:
            if row.get("category") == category:
                return int(row["downloads"])
    except (KeyError, TypeError, ValueError):
        return None
    return None


def cmd_report(args: argparse.Namespace) -> int:
    """Aggregate chunk JSONs + pypistats snapshots into report.md / report.json."""
    chunks: list[dict[str, Any]] = []
    for path in sorted(Path(args.results).glob("result-*.json")):
        try:
            chunks.append(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            print(f"warning: bad chunk {path}: {exc}", file=sys.stderr)

    if not chunks:
        print("error: no chunk results found in --results directory", file=sys.stderr)
        # Still render an empty-but-valid report so the run summary isn't blank.

    pre = json.loads(Path(args.pre).read_text()) if Path(args.pre).exists() else {}
    post = json.loads(Path(args.post).read_text()) if Path(args.post).exists() else {}

    metrics = _compute_metrics(chunks)
    pypi_stats = _compute_pypi_stats(pre, post)

    md = _render_markdown(args, metrics, pypi_stats, chunks)
    Path(args.md).write_text(md)

    out = {
        "config": {
            "installs": args.installs,
            "concurrency": args.concurrency,
            "version": args.version,
            "run_url": args.run_url,
        },
        "metrics": metrics,
        "pypi_stats": pypi_stats,
        "chunks": chunks,
    }
    Path(args.json).write_text(json.dumps(out, indent=2) + "\n")

    print(f"Wrote {args.md} and {args.json}")
    return 0


def _compute_metrics(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    total_success = sum(int(c.get("success", 0)) for c in chunks)
    total_failure = sum(int(c.get("failure", 0)) for c in chunks)
    all_durations: list[int] = [
        int(d) for c in chunks for d in (c.get("durations_ms") or []) if isinstance(d, (int, float))
    ]
    chunk_walls = [int(c.get("wall_ms", 0)) for c in chunks]
    longest_chunk_wall = max(chunk_walls) if chunk_walls else 0

    duration_total = sum(all_durations)
    effective_parallelism: float | None = None
    if longest_chunk_wall > 0:
        effective_parallelism = round(duration_total / longest_chunk_wall, 1)

    attempted = total_success + total_failure
    success_rate = round(100 * total_success / attempted, 2) if attempted else 0.0

    return {
        "chunks_observed": len(chunks),
        "attempted": attempted,
        "success": total_success,
        "failure": total_failure,
        "success_rate_pct": success_rate,
        "duration_total_ms": duration_total,
        "duration_avg_ms": round(mean(all_durations)) if all_durations else None,
        "duration_p50_ms": _percentile(all_durations, 50),
        "duration_p90_ms": _percentile(all_durations, 90),
        "duration_p99_ms": _percentile(all_durations, 99),
        "duration_min_ms": min(all_durations) if all_durations else None,
        "duration_max_ms": max(all_durations) if all_durations else None,
        "wall_ms_max_chunk": longest_chunk_wall or None,
        "effective_parallelism": effective_parallelism,
    }


def _compute_pypi_stats(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    pre_day, pre_week, pre_month = (_recent(pre, k) for k in ("last_day", "last_week", "last_month"))
    post_day, post_week, post_month = (_recent(post, k) for k in ("last_day", "last_week", "last_month"))

    pre_with_mirrors = _overall(pre, "with_mirrors")
    pre_no_mirrors = _overall(pre, "without_mirrors")
    post_with_mirrors = _overall(post, "with_mirrors")
    post_no_mirrors = _overall(post, "without_mirrors")

    def _pair(before: int | None, after: int | None) -> dict[str, int | None]:
        delta = (after - before) if (before is not None and after is not None) else None
        return {"before": before, "after": after, "delta": delta}

    return {
        "pre_ts": pre.get("ts"),
        "post_ts": post.get("ts"),
        "last_day": _pair(pre_day, post_day),
        "last_week": _pair(pre_week, post_week),
        "last_month": _pair(pre_month, post_month),
        "overall_with_mirrors": _pair(pre_with_mirrors, post_with_mirrors),
        "overall_without_mirrors": _pair(pre_no_mirrors, post_no_mirrors),
    }


def _render_markdown(
    args: argparse.Namespace,
    metrics: dict[str, Any],
    pypi: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> str:
    lines: list[str] = []

    lines.append("# PyPI install load-test report")
    lines.append("")
    lines.append(
        "> **Legitimate load test.** Real `pip install bernstein` invocations against "
        "the live PyPI index, run from GitHub-hosted runners. Designed to surface "
        "(a) Actions parallelism behaviour and (b) how installs register on "
        "[pypistats.org/packages/bernstein](https://pypistats.org/packages/bernstein). "
        "Not a stats-inflation tool — pypistats classifies CI traffic into the "
        "`with_mirrors` bucket; the headline `without_mirrors` column may not move "
        "at all, by design."
    )
    lines.append("")

    lines.append("## Run configuration")
    lines.append("")
    lines.append(f"- **Run URL**: {args.run_url or '—'}")
    lines.append(f"- **Target installs**: {args.installs:,}")
    lines.append(f"- **Concurrency cap**: {args.concurrency}")
    lines.append(f"- **Version**: `{args.version}`")
    lines.append(f"- **Chunks observed**: {metrics['chunks_observed']}")
    lines.append("")

    lines.append("## Install metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | ---: |")
    lines.append(f"| Attempted | {_fmt_int(metrics['attempted'])} |")
    lines.append(f"| Success | {_fmt_int(metrics['success'])} ({metrics['success_rate_pct']}%) |")
    lines.append(f"| Failures | {_fmt_int(metrics['failure'])} |")
    lines.append(f"| Total install time (sum of all chunks) | {_fmt_ms(metrics['duration_total_ms'])} |")
    lines.append(f"| Per-install avg | {_fmt_ms(metrics['duration_avg_ms'])} |")
    lines.append(f"| Per-install p50 | {_fmt_ms(metrics['duration_p50_ms'])} |")
    lines.append(f"| Per-install p90 | {_fmt_ms(metrics['duration_p90_ms'])} |")
    lines.append(f"| Per-install p99 | {_fmt_ms(metrics['duration_p99_ms'])} |")
    lines.append(
        f"| Per-install min / max | {_fmt_ms(metrics['duration_min_ms'])} / {_fmt_ms(metrics['duration_max_ms'])} |"
    )
    lines.append(f"| Slowest chunk wall-clock | {_fmt_ms(metrics['wall_ms_max_chunk'])} |")
    parallelism = (
        f"{metrics['effective_parallelism']}×"  # noqa: RUF001 — multiplication sign is intentional in the report
        if metrics["effective_parallelism"] is not None
        else "—"
    )
    lines.append(f"| Effective parallelism | {parallelism} |")
    lines.append("")
    lines.append(
        "> *Effective parallelism* = sum of all install durations ÷ longest "
        "chunk's wall clock. When the runner pool is healthy this approaches the "
        "configured `concurrency` input. When it falls below, the runner pool is "
        "throttled (other jobs sharing the org's pool, or GitHub-side capacity dips)."
    )
    lines.append("")

    lines.append("## pypistats.org snapshot")
    lines.append("")
    lines.append(f"- **Pre-test**: `{pypi.get('pre_ts') or '—'}`")
    lines.append(f"- **Post-test**: `{pypi.get('post_ts') or '—'}`")
    lines.append("")
    lines.append("| Counter | Before | After | Δ |")
    lines.append("| --- | ---: | ---: | ---: |")
    rows = [
        ("Last day (recent)", "last_day"),
        ("Last week (recent)", "last_week"),
        ("Last month (recent)", "last_month"),
        ("Overall, with mirrors (CI counted)", "overall_with_mirrors"),
        ("Overall, without mirrors (humans only)", "overall_without_mirrors"),
    ]
    for label, key in rows:
        cell = pypi.get(key, {})
        lines.append(
            f"| {label} | {_fmt_int(cell.get('before'))} | "
            f"{_fmt_int(cell.get('after'))} | {_delta(cell.get('after'), cell.get('before'))} |"
        )
    lines.append("")
    lines.append(
        "> **pypistats has a ~24-hour ingestion lag.** A delta near 0 immediately "
        "after the run is normal. Re-check tomorrow at "
        "https://pypistats.org/packages/bernstein for the registered counts."
    )
    lines.append("")

    failures = [c for c in chunks if int(c.get("failure", 0)) > 0]
    if failures:
        lines.append("## Failures by chunk")
        lines.append("")
        lines.append("| Chunk | Success | Failure | First error |")
        lines.append("| ---: | ---: | ---: | --- |")
        for c in sorted(failures, key=lambda x: int(x.get("chunk", 0))):
            err_lines = (c.get("errors") or "").strip().splitlines()
            err_str = err_lines[0][:120] if err_lines else ""
            err_str = err_str.replace("|", "\\|")  # don't break the table
            lines.append(f"| {c.get('chunk')} | {c.get('success', 0)} | {c.get('failure', 0)} | `{err_str}` |")
        lines.append("")

    lines.append("## How to interpret")
    lines.append("")
    lines.append(
        "- **`without_mirrors` did not move.** Expected. pypistats classifies CI "
        "runners into the mirrors bucket. Check `with_mirrors` and the daily "
        "aggregate at https://pypistats.org/packages/bernstein in 24-48h."
    )
    lines.append(
        "- **`without_mirrors` moved noticeably.** Some installs were classified as "
        "human; treat the magnitude with skepticism — it suggests the IP heuristic "
        "missed."
    )
    lines.append(
        "- **`Last day` jumped by ≈ install count.** The fan-out hit the daily "
        "window cleanly. Compare against `with_mirrors` Δ for sanity."
    )
    lines.append(
        "- **Effective parallelism < concurrency cap.** Runner pool was throttled; "
        "lower the cap or try a different time of day."
    )
    lines.append(
        "- **Failures > 1%.** Inspect the `errors` strings — most common are PyPI "
        "5xx blips and runner DNS hiccups; both retry-friendly."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PyPI install load-test helper (run + report modes)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run",
        help="Perform N sequential pip installs locally and emit a chunk JSON.",
    )
    p_run.add_argument("--installs", type=int, default=10, help="Number of installs to run.")
    p_run.add_argument("--chunk", type=int, default=1, help="Chunk index (for naming only).")
    p_run.add_argument("--version", default="", help="Optional pinned version (e.g. 1.9.1).")
    p_run.add_argument(
        "--tmp",
        default="/tmp/pypi-load-test",
        help="Scratch directory for venvs (cleaned up after each install).",
    )
    p_run.add_argument(
        "--output",
        default="result-1.json",
        help="Where to write the chunk JSON.",
    )
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser(
        "report",
        help="Render Markdown + JSON report from chunk JSONs and pypistats snapshots.",
    )
    p_rep.add_argument("--results", required=True, help="Directory of result-*.json files.")
    p_rep.add_argument("--pre", required=True, help="Pre-test pypistats snapshot JSON.")
    p_rep.add_argument("--post", required=True, help="Post-test pypistats snapshot JSON.")
    p_rep.add_argument("--installs", type=int, default=0, help="Configured install target.")
    p_rep.add_argument("--concurrency", type=int, default=0, help="Configured concurrency cap.")
    p_rep.add_argument("--version", default="latest", help="Version label for the report.")
    p_rep.add_argument("--run-url", default="", help="GitHub Actions run URL for the report header.")
    p_rep.add_argument("--md", required=True, help="Output path for the Markdown report.")
    p_rep.add_argument("--json", required=True, help="Output path for the JSON report.")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
